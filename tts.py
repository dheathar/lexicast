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

SAMPLE_RATE = 24000  # Kokoro output rate

# Trailing pause after a block, by label (ms)
BLOCK_PAUSE = {"header": 1000, "caption": 500, "body": 200, "other": 0}
CHUNK_PAUSE = 50  # between chunks within a block


def split_text(text, max_length=250):
    """Split text into <= max_length pieces, preferring sentence boundaries."""
    text = re.sub(r"\s+", " ", text.strip())
    chunks = []

    sentences = re.split(r"(?<=[\.\?\!;])\s+", text)
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
                       voice="af_heart", lang="a", speed=1.0,
                       timeline_path=TIMELINE_JSON, skip_other=True,
                       progress=None, clean=True):
    from kokoro import KPipeline  # imported lazily so --help etc. stay fast

    os.makedirs(temp_folder, exist_ok=True)
    if clean:  # remove any stale block_*.wav so an old run can't leak into the join
        import glob
        for old in glob.glob(os.path.join(temp_folder, "block_*.wav")):
            os.remove(old)
    pipeline = KPipeline(lang_code=lang)

    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    timeline = []
    cursor = 0  # global position in samples; matches block concat order

    for i, block in enumerate(data, start=1):
        if progress:
            progress(i, len(data), "synthesize")
        label = block.get("label", "body")
        text = (block.get("text") or "").strip()
        if not text or (skip_other and label == "other"):
            continue

        print(f"Processing block {i}/{len(data)} [{label}]...")
        chunks = split_text(text, max_length=250)
        block_parts = []
        block_start = cursor

        for j, chunk in enumerate(chunks):
            print(f"  synth block {i} chunk {j} ({len(chunk)} chars)")
            # Kokoro may split a chunk internally; concatenate its pieces.
            pieces = [audio for _, _, audio in pipeline(chunk, voice=voice, speed=speed)]
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
                "text": chunk,
                "start_ms": round(seg_start / SAMPLE_RATE * 1000),
                "end_ms": round(cursor / SAMPLE_RATE * 1000),
            })
            cursor += len(sil)

        block_audio = np.concatenate(block_parts) if block_parts else _silence(0)
        block_path = os.path.join(temp_folder, f"block_{i}.wav")
        sf.write(block_path, block_audio, SAMPLE_RATE)
        print(f"  saved {block_path}  (block @ {block_start/SAMPLE_RATE:.1f}s)")

    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    total_s = cursor / SAMPLE_RATE
    print(f"✅ {len(timeline)} segments, ~{total_s/60:.1f} min. "
          f"Audio in /{temp_folder}, timeline -> {timeline_path}")
    return timeline


if __name__ == "__main__":
    generate_audiobook()
