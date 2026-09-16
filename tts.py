"""Text-to-speech synthesis with Kokoro-82M.

Reads classified_text.json, synthesizes each block, and writes:
  * temp/block_<i>.wav            individual block audio (resumable, joined later)
  * timeline.json                 per-segment text + global start/end ms

The timeline is what powers the synced HTML: every synthesized sentence is one
entry whose start/end is its exact position in the concatenated audio, so the
final book audio and the highlight track stay in sync without any alignment model.

Kokoro is fast on CPU/Apple-Silicon and needs no API key. Pick a voice with
--voice (e.g. af_heart, af_bella, am_michael, bf_emma) and a language with
--lang (a=US English, b=British, e=Spanish, f=French, i=Italian, p=Portuguese,
h=Hindi, j=Japanese, z=Mandarin).
"""

import json
import os
import re

import numpy as np
import soundfile as sf

INPUT_JSON = "classified_text.json"
TEMP_FOLDER = "temp"
TIMELINE_JSON = "timeline.json"

SAMPLE_RATE = 24000  # Kokoro output rate (Google chunks request the same)

# Trailing pause after a block, by label (ms)
BLOCK_PAUSE = {"header": 1000, "caption": 500, "body": 200, "other": 0}
CHUNK_PAUSE = 50  # between chunks within a block

# --- Google Cloud TTS engine (added 2026-09-02) ---------------------------
# Kokoro has no Greek voice, so chunks that are mostly Greek script are
# routed to Google Cloud TTS. lang="auto" (the webapp default) routes per
# chunk by script ratio; lang="el" forces Google for every chunk; any other
# Kokoro lang code stays pure Kokoro. Default voice el-GR-Wavenet-B: 4M free
# chars/month, the cheapest tier. A local monthly counter (jobs/_gcp_usage.json)
# hard-caps characters below the free tier so billing can never start by
# accident; Chirp3-HD voices have the smaller 1M free bucket and get a
# proportionally smaller cap.

import base64
import datetime
import io as _io

GOOGLE_TTS_VOICE = os.environ.get("GOOGLE_TTS_VOICE", "el-GR-Wavenet-B")
USAGE_PATH = os.environ.get("GCP_USAGE_PATH",
                            os.path.join("jobs", "_gcp_usage.json"))


def _month_cap(voice):
    override = os.environ.get("GOOGLE_TTS_MONTHLY_LIMIT")
    if override:
        return int(override)
    return 900_000 if "Chirp3" in voice else 3_800_000


def _usage_add(n_chars):
    month = datetime.date.today().strftime("%Y-%m")
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            usage = json.load(f)
    except (FileNotFoundError, ValueError):
        usage = {}
    cur = usage.get(month, 0)
    if cur + n_chars > _month_cap(GOOGLE_TTS_VOICE):
        raise RuntimeError(
            f"Google TTS free-tier guard: this job would push this month to "
            f"{cur + n_chars:,} chars (cap {_month_cap(GOOGLE_TTS_VOICE):,} "
            f"for {GOOGLE_TTS_VOICE}). Refusing to synthesize to avoid "
            f"billing; wait for next month or raise GOOGLE_TTS_MONTHLY_LIMIT.")
    usage[month] = cur + n_chars
    os.makedirs(os.path.dirname(USAGE_PATH) or ".", exist_ok=True)
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2)


_gcp_creds = None


def _google_synth(text, voice, speed):
    """One chunk via Google Cloud TTS -> float32 mono numpy at SAMPLE_RATE.
    LINEAR16 @ 24 kHz is requested so Google blocks join with Kokoro blocks
    without resampling."""
    global _gcp_creds
    import requests
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "gcp-tts.json")
    if _gcp_creds is None or not _gcp_creds.valid:
        _gcp_creds = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _gcp_creds.refresh(gtr.Request())

    _usage_add(len(text))  # count before the call: never undercount budget
    r = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {_gcp_creds.token}"},
        json={
            "input": {"text": text},
            "voice": {"languageCode": voice[:5], "name": voice},
            "audioConfig": {"audioEncoding": "LINEAR16",
                            "sampleRateHertz": SAMPLE_RATE,
                            "speakingRate": max(0.25, min(4.0, speed))},
        }, timeout=60)
    r.raise_for_status()
    raw = base64.b64decode(r.json()["audioContent"])
    data, sr = sf.read(_io.BytesIO(raw), dtype="float32")
    if data.ndim > 1:
        data = data[:, 0]
    if sr != SAMPLE_RATE:
        raise RuntimeError(f"unexpected Google sample rate {sr}")
    return data


_GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _greek_ratio(text):
    g = len(_GREEK_RE.findall(text))
    l = len(_LATIN_RE.findall(text))
    return g / (g + l) if (g + l) else 0.0


def split_text(text, max_length=250, is_header=False):
    """Split text into <= max_length pieces, preferring sentence boundaries.

    is_header=True skips the sentence-boundary split: a heading is one utterance,
    and a numbered heading like "1. Opening: what this annex supports" would
    otherwise be cut right after "1." because the sentence regex treats a digit
    followed by "." and whitespace as a sentence end. Length-based fallback
    splitting still applies if a header is pathologically long.
    """
    text = re.sub(r"\s+", " ", text.strip())
    chunks = []

    sentences = [text] if is_header else re.split(r"(?<=[\.\?\!;])\s+", text)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_length:
            chunks.append(sentence)
            continue
        # Too long: split on commas
        subparts = re.split(r"(?<=,)\s+", sentence)
        temp = ""
        for part in subparts:
            if len(temp) + len(part) + 1 <= max_length:
                temp += (" " + part if temp else part)
            else:
                if temp:
                    chunks.append(temp.strip())
                temp = part
        if temp:
            chunks.append(temp.strip())

    # Still too long: split on word boundaries (never mid-word)
    final = []
    for chunk in chunks:
        if len(chunk) <= max_length:
            final.append(chunk)
            continue
        temp = ""
        for word in chunk.split(" "):
            if len(temp) + len(word) + 1 <= max_length:
                temp += (" " + word if temp else word)
            else:
                if temp:
                    final.append(temp.strip())
                temp = word
        if temp:
            final.append(temp.strip())
    return final


def _silence(ms):
    return np.zeros(int(SAMPLE_RATE * ms / 1000), dtype=np.float32)


def generate_audiobook(input_json=INPUT_JSON, temp_folder=TEMP_FOLDER,
                       voice="af_heart", lang="auto", speed=1.0,
                       timeline_path=TIMELINE_JSON, skip_other=True,
                       progress=None, clean=True, cache=None, cache_dir=None,
                       google_voice=None, progressive_path=None):
    """cache: optional {block_hash: manifest_entry} from audio_cache.load_manifest
    -- a block whose `_cache_hash` is a key here is NOT re-synthesized; its cached
    wav is copied into this run's temp_folder at the correct block_<i>.wav slot
    (join_audios.join globs+sorts by that index, so this needs no change there)
    and its cached chunk records (relative offsets) are re-added to the timeline
    shifted by the current cursor. cache_dir: where to write newly-synthesized
    blocks' wavs so a future run can reuse them (see webapp.py's incremental
    re-conversion path, added 2026-08-29). Both are None for a normal full run.

    progressive_path: optional live per-block manifest (2026-09-05) for
    streaming playback -- written atomically after EVERY completed block
    (cache hits included), with per-chunk timings relative to the block start.
    Invariant: an entry appears only after its wav is fully on disk in
    temp_folder, so a reader can start fetching block audio the moment it
    shows up, long before the join/synced phases run.

    Returns (timeline, new_entries) -- new_entries holds a manifest entry for
    every block that was actually synthesized this run (empty dict if cache is
    None), for the caller to merge into the persisted cache manifest.
    """
    os.makedirs(temp_folder, exist_ok=True)
    if clean:  # remove any stale block_*.wav so an old run can't leak into the join
        import glob
        for old in glob.glob(os.path.join(temp_folder, "block_*.wav")):
            os.remove(old)

    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Kokoro's model load is real setup cost -- skip it entirely on a run
    # where every block is a cache hit (the common case mid-report: only the
    # newly-added section needs synthesis at all).
    pipeline = None

    def _pipeline():
        nonlocal pipeline
        if pipeline is None:
            from kokoro import KPipeline  # imported lazily so --help etc. stay fast
            # "auto"/"el" are not Kokoro codes: auto routes English-ish chunks
            # to Kokoro with the US English phonemizer, el never reaches Kokoro
            pipeline = KPipeline(lang_code="a" if lang in ("auto", "el") else lang)
        return pipeline

    timeline = []
    new_entries = {}
    cursor = 0  # global position in samples; matches block concat order

    # Live per-block manifest for streaming playback (see docstring). os.replace
    # makes each update atomic -- a concurrent reader (the /manifest endpoint)
    # always sees either the previous or the new complete file, never a torn one.
    prog_blocks = []

    def _prog_entry(i, label, section, wav_name, duration_ms, chunk_recs):
        return {
            "i": i, "wav": wav_name, "duration_ms": round(duration_ms),
            "label": label, "section": section or "",
            "chunks": [{"text": c["text"], "level": c.get("level"),
                        "start_ms": round(c["rel_start"] / SAMPLE_RATE * 1000),
                        "end_ms": round(c["rel_end"] / SAMPLE_RATE * 1000)}
                       for c in chunk_recs],
        }

    def _write_progressive():
        if not progressive_path:
            return
        tmp = progressive_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sample_rate": SAMPLE_RATE, "blocks": prog_blocks},
                      f, ensure_ascii=False)
        os.replace(tmp, progressive_path)

    _write_progressive()  # empty baseline: synthesis started, nothing ready yet

    for i, block in enumerate(data, start=1):
        if progress:
            progress(i, len(data), "synthesize")
        label = block.get("label", "body")
        text = (block.get("text") or "").strip()
        if not text or (skip_other and label == "other"):
            continue

        h = block.get("_cache_hash")
        cache_entry = cache.get(h) if (cache and h) else None
        if cache_entry and cache_dir:
            src = os.path.join(cache_dir, cache_entry["wav"])
            if os.path.isfile(src):
                block_start = cursor
                info = sf.info(src)
                block_path = os.path.join(temp_folder, f"block_{i}.wav")
                import shutil
                shutil.copyfile(src, block_path)
                for c in cache_entry["chunks"]:
                    timeline.append({
                        "index": len(timeline),
                        "block": i,
                        "label": c["label"],
                        "section": c["section"],
                        "level": c.get("level"),
                        "text": c["text"],
                        "start_ms": round((block_start + c["rel_start"]) / SAMPLE_RATE * 1000),
                        "end_ms": round((block_start + c["rel_end"]) / SAMPLE_RATE * 1000),
                    })
                cursor += info.frames
                if progressive_path:
                    prog_blocks.append(_prog_entry(
                        i, label, block.get("section", ""), f"block_{i}.wav",
                        info.frames / SAMPLE_RATE * 1000, cache_entry["chunks"]))
                    _write_progressive()
                print(f"  block {i}/{len(data)}: cache hit, reusing {cache_entry['wav']}"
                      f"  (block @ {block_start/SAMPLE_RATE:.1f}s)")
                continue  # skip synthesis entirely for this block

        print(f"Processing block {i}/{len(data)} [{label}]...")
        chunks = split_text(text, max_length=250, is_header=(label == "header"))
        block_parts = []
        block_start = cursor
        chunk_records = []  # relative-to-block-start, for the cache entry

        for j, chunk in enumerate(chunks):
            print(f"  synth block {i} chunk {j} ({len(chunk)} chars)")
            use_google = (lang == "el"
                          or (lang == "auto" and _greek_ratio(chunk) > 0.3))
            if use_google:
                audio = _google_synth(chunk, google_voice or GOOGLE_TTS_VOICE,
                                      speed)
            else:
                # Kokoro may split a chunk internally; concatenate its pieces.
                pieces = [audio for _, _, audio in _pipeline()(chunk, voice=voice, speed=speed)]
                audio = np.concatenate(pieces).astype(np.float32) if pieces else _silence(0)

            seg_start = cursor
            cursor += len(audio)
            block_parts.append(audio)

            pause = CHUNK_PAUSE if j < len(chunks) - 1 else BLOCK_PAUSE.get(label, 200)
            sil = _silence(pause)
            block_parts.append(sil)

            timeline.append({
                "index": len(timeline),
                "block": i,
                "label": label,
                "section": block.get("section", ""),
                "level": block.get("level"),
                "text": chunk,
                "start_ms": round(seg_start / SAMPLE_RATE * 1000),
                "end_ms": round(cursor / SAMPLE_RATE * 1000),
            })
            chunk_records.append({
                "label": label,
                "section": block.get("section", ""),
                "level": block.get("level"),
                "text": chunk,
                "rel_start": seg_start - block_start,
                "rel_end": cursor - block_start,
            })
            cursor += len(sil)

        block_audio = np.concatenate(block_parts) if block_parts else _silence(0)
        block_path = os.path.join(temp_folder, f"block_{i}.wav")
        sf.write(block_path, block_audio, SAMPLE_RATE)
        print(f"  saved {block_path}  (block @ {block_start/SAMPLE_RATE:.1f}s)")

        if cache_dir and h:
            os.makedirs(cache_dir, exist_ok=True)
            cache_wav_name = f"{h}.wav"
            sf.write(os.path.join(cache_dir, cache_wav_name), block_audio, SAMPLE_RATE)
            new_entries[h] = {
                "wav": cache_wav_name,
                "normalized_text": text,
                "chunks": chunk_records,
            }

        # Only after the wav (and cache copy) are fully on disk does the block
        # become visible to streamers -- the progressive-manifest invariant.
        if progressive_path:
            prog_blocks.append(_prog_entry(
                i, label, block.get("section", ""), f"block_{i}.wav",
                len(block_audio) / SAMPLE_RATE * 1000, chunk_records))
            _write_progressive()

    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    total_s = cursor / SAMPLE_RATE
    print(f"✅ {len(timeline)} segments, ~{total_s/60:.1f} min. "
          f"Audio in /{temp_folder}, timeline -> {timeline_path}")
    return timeline, new_entries


if __name__ == "__main__":
    generate_audiobook()
