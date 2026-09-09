"""The /transcribe pipeline: recording -> personalized transcript -> notes ->
synced player -> archive staging. This is the DEEP path of the personalized
STT design (speech-personal/arch.txt Layer 0): asynchronous batch jobs. The
live fast path (push-to-talk dictation) is deliberately not a web-service
job and stays outside lexicast.

Two-pass decode (Layer 3): pass 1 transcribes plain, the hypothesis text is
phonetically matched against lexicon.db (espeak-ng IPA edit distance), and
the winning terms are fed back as faster-whisper `hotwords` for pass 2.
Pass 2 is skipped when pass 1 surfaces no lexicon candidates, so clean audio
never pays double transcription cost.

Diarization (pyannote) is deliberately not in this image; transcripts are
unlabeled. Layer 4 (confidence-gated patch post-editor) and Layer 5 (the
/transcribe/<job>/corrections feedback endpoint) land later.

Every stage funnels through prog(cur, total, phase) and check_cancel() --
cooperative cancellation at stage/segment boundaries, same contract as the
audiobook pipeline.
"""

import datetime
import json
import os
import re
import shutil
import subprocess

import extract_audio
import transcribe as asr
import session2notes as s2n
import extract_notes
import lexicon

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_PENDING = os.path.join(APP_DIR, "archive_pending")

DEFAULT_ASR_MODEL = "large-v3-turbo"
RETRIEVE_CHAR_BUDGET = 20000   # phonetic scan at most this much of pass-1 text
MAX_HOTWORDS = 40              # hotwords beyond ~50 degrade decoding

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".flac", ".ogg",
              ".opus", ".webm", ".aac", ".wma", ".mkv", ".avi"}

ARCHIVE_FILES = ("audio.mp3", "transcript.txt", "transcript.srt",
                 "transcript.vtt", "transcript.json", "notes.md",
                 "notes.json", "transcript.html")


def _slug(s, fallback="capture"):
    s = re.sub(r"[^A-Za-z0-9\u0370-\u03ff_-]+", "-", (s or "").strip()).strip("-")
    return (s or fallback)[:60]


def _archive_stage(jd, spec, title):
    """Copy deliverables into archive_pending/<stamp>_<slug>/ -- the local
    half of the audio-memories archive. The scheduled flush task (every
    15 min) moves it to the network share when reachable. Archiving is
    best-effort by design and NEVER fails a job."""
    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        dst = os.path.join(ARCHIVE_PENDING, f"{stamp}_{_slug(title)}")
        os.makedirs(dst, exist_ok=True)
        # the ORIGINAL upload is the archive's point -- notes can be
        # regenerated, the raw voice cannot
        shutil.copy2(spec["path"],
                     os.path.join(dst, "original" + os.path.splitext(spec["path"])[1].lower()))
        for fn in ARCHIVE_FILES:
            src = os.path.join(jd, fn)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dst, fn))
        return os.path.basename(dst)
    except OSError:
        return None


def run(jd, spec, prog, check_cancel):
    """Run the full pipeline. jd = job dir; spec = {path, language,
    asr_model, notes_model, title}. Returns a summary dict merged into the
    job record (visible via /status/<job_id>)."""
    src = spec["path"]
    lang_arg = None if spec["language"] in ("auto", "", None) else spec["language"]

    # 1. audio -> 16kHz wav + a 96k mp3 for the player/archive
    prog(0, 1, "extract-audio")
    check_cancel()
    wav = extract_audio.to_wav(src, os.path.join(jd, "audio.16k.wav"))
    audio_mp3 = os.path.join(jd, "audio.mp3")
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "96k", audio_mp3],
                   check=True, capture_output=True)

    # 2. ASR pass 1 (plain)
    check_cancel()
    segs, dur, lang = asr.transcribe(
        wav, model_size=spec["asr_model"], language=lang_arg,
        engine="faster-whisper", device="cpu", compute_type="int8",
        vad=True, progress=prog)

    # 3. lexicon retrieval (Layer 3): what did pass 1 *almost* say?
    check_cancel()
    prog(0, 1, "lexicon")
    hyp = " ".join(s["text"] for s in segs)[:RETRIEVE_CHAR_BUDGET]
    hotwords = []
    if hyp.strip():
        conn = lexicon.connect()
        try:
            hits = lexicon.retrieve(conn, hyp, k=MAX_HOTWORDS, record=True)
            hotwords = [h["term"] for h in hits]
        finally:
            conn.close()

    # 4. ASR pass 2, biased with the personal vocabulary. NOTE: faster-whisper
    # wants hotwords as a STRING (it calls .strip() internally) -- passing the
    # list crashes pass 2 with AttributeError (real bug, 2026-09-09).
    if hotwords:
        check_cancel()
        prog(0, 1, "asr-pass2")
        segs, dur, lang = asr.transcribe(
            wav, model_size=spec["asr_model"], language=lang_arg,
            engine="faster-whisper", device="cpu", compute_type="int8",
            vad=True, progress=prog, hotwords=" ".join(hotwords))

    # 5. transcript files
    check_cancel()
    prog(0, 1, "transcript")
    s2n.write_transcript_files(segs, jd, diarized=False)

    # 6. notes via Ollama (summary / takeaways / decisions / action items /
    #    topics / qa) -- the session2notes notes engine, unchanged
    check_cancel()
    notes = extract_notes.extract_notes(s2n.transcript_for_llm(segs, diarized=False),
                                        model=spec["notes_model"], progress=prog)
    with open(os.path.join(jd, "notes.json"), "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    with open(os.path.join(jd, "notes.md"), "w", encoding="utf-8") as f:
        f.write(extract_notes.notes_to_markdown(notes, title=spec["title"]))

    # 7. synced player (audio embedded -- standalone deliverable)
    check_cancel()
    prog(0, 1, "synced")
    title = notes.get("title") or spec["title"]
    s2n.build_synced_html(segs, jd, audio_mp3, title, embed=True)

    # 8. archive staging (best-effort)
    prog(0, 1, "archive")
    archived = _archive_stage(jd, spec, title)

    return {"language": lang, "duration_s": round(dur or 0, 1),
            "segments": len(segs), "hotwords": hotwords,
            "title": title, "archive": archived}
