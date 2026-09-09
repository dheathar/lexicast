"""Backend-only API for lexicast -- no browser UI (stripped 2026-08-28; the
only caller is now the `lexicast` Claude Code skill, driving this over HTTP).

POST a PDF / .tex / .docx / .md to /convert, poll /status/<job_id>, fetch
results from /files/<job_id>/<name>. Runs the pipeline (extract -> Ollama
normalize -> Kokoro TTS -> join -> synced) in a background worker.

Run (inside the Docker image): python webapp.py  ->  API on :5005

Single-user / localhost tool: one job runs at a time (TTS is heavy); further
submissions queue.
"""

import json
import os
import queue
import threading
import traceback
import uuid

from flask import (Flask, Response, abort, jsonify, request,
                   send_from_directory)
from markupsafe import escape

import extract_text
import classify
import extract_markdown
import normalize as normalize_mod
import tts
import join_audios
import make_synced
import audio_cache
import stt_pipeline

APP_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(APP_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

ALLOWED = {".pdf", ".tex", ".latex", ".docx", ".md", ".markdown"}

app = Flask(__name__)
# 512 MB (was 64 MB): /transcribe accepts long voice captures, and an hour of
# m4a/opus lands in the 30-60 MB range with headroom for video uploads.
# Werkzeug spools large bodies to a temp file, so this costs no RAM.
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


@app.errorhandler(Exception)
def _json_errors(e):
    """abort(400, "...") etc. otherwise render Werkzeug's default HTML error
    page, which the frontend's JSON.parse() then chokes on (real bug hit
    2026-08-28: a .docx upload correctly got rejected server-side, but the
    browser saw "Unexpected token '<', \"<!doctype \"... is not valid JSON"
    instead of the actual "unsupported type" message). Every error path now
    returns JSON so the frontend can always show the real reason.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(error=e.description or e.name), e.code
    app.logger.exception("unhandled error")
    return jsonify(error=str(e)), 500

jobs = {}                       # id -> dict(status,...)
jobs_lock = threading.Lock()
work_q = queue.Queue()
cancel_events = {}              # id -> threading.Event(), set by /cancel


class JobCancelled(Exception):
    """Raised cooperatively from inside prog() -- there's no safe way to kill
    a Python thread mid-operation, so cancellation is checked at each block
    boundary (extract/normalize/synthesize all call prog() per block) instead
    of being forced immediately."""


def _status_path(job_id):
    return os.path.join(JOBS_DIR, job_id, "status.json")


def _set(job_id, **kw):
    with jobs_lock:
        jobs[job_id].update(kw)
        snapshot = dict(jobs[job_id])
    # Persisted alongside the job's own output files (already volume-mounted)
    # so /status survives a container restart -- `jobs` was purely in-memory
    # until 2026-08-28, which meant ANY restart (a crash, a Docker Desktop
    # hiccup -- both observed repeatedly on this machine) silently wiped
    # tracking for every job, including ones that had already finished and
    # whose output files were still sitting right there on disk. Best-effort:
    # if this write itself fails (e.g. mid-crash), the in-memory update above
    # still happened, so the in-process behavior is unaffected either way.
    try:
        with open(_status_path(job_id), "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
    except OSError:
        pass


def _load_persisted_jobs():
    """Reconstruct `jobs` from each job dir's status.json on startup. A job
    that was still queued/running/cancelling when the process died can't be
    safely resumed (Kokoro state, partial temp/ wavs, an in-flight Ollama
    call -- none of that survived), so those are honestly reported as failed
    rather than left as a phantom 404 or a progress bar that never moves
    again. Completed/errored/cancelled jobs are restored as-is."""
    if not os.path.isdir(JOBS_DIR):
        return
    for job_id in os.listdir(JOBS_DIR):
        path = _status_path(job_id)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") in ("queued", "running", "cancelling"):
            data["status"] = "error"
            data["error"] = "Server restarted mid-job -- state not resumable, resubmit."
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        jobs[job_id] = data


_load_persisted_jobs()


def _run_job(job_id, spec):
    jd = os.path.join(JOBS_DIR, job_id)
    classified = os.path.join(jd, "classified_text.json")
    timeline = os.path.join(jd, "timeline.json")
    audio = os.path.join(jd, "audiobook.mp3")
    synced = os.path.join(jd, "synced.html")
    import json

    cancel_ev = cancel_events[job_id]

    def check_cancel():
        if cancel_ev.is_set():
            raise JobCancelled()

    def prog(cur, total, phase):
        check_cancel()
        _set(job_id, phase=phase, current=cur, total=total,
             pct=round(cur / total * 100) if total else 0)

    try:
        # 1. Extract
        _set(job_id, status="running", phase="extract", current=0, total=0, pct=0)
        check_cancel()
        ext = os.path.splitext(spec["path"])[1].lower()
        if ext == ".pdf":
            data = extract_text.extract_from_pdf(spec["path"])
            data = classify.classify_font_sizes(data)
        else:
            data = extract_markdown.extract(spec["path"])
        with open(classified, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        narrated = sum(1 for b in data if b.get("text", "").strip()
                       and b.get("label") != "other")
        _set(job_id, blocks=narrated)

        # Incremental re-conversion (2026-08-29): tag each narratable block
        # with a content hash (raw text + label + section) so normalize/tts
        # below can skip anything unchanged since the last run of this same
        # doc_id. A voice/lang/speed/normalize-model change invalidates the
        # WHOLE cache for this doc_id (see audio_cache.load_manifest) -- never
        # a partial mix, since splicing audio made at two different settings
        # is audibly broken.
        for b in data:
            text = (b.get("text") or "").strip()
            if text and b.get("label") != "other":
                b["_cache_hash"] = audio_cache.block_hash(
                    text, b.get("label", ""), b.get("section", ""))
        profile = {
            "voice": spec["voice"], "lang": spec["lang"], "speed": spec["speed"],
            "normalize": spec["normalize"],
            "normalize_model": spec["normalize_model"] if spec["normalize"] else None,
            "google_voice": spec.get("google_voice"),
        }
        manifest = audio_cache.load_manifest(JOBS_DIR, spec["doc_id"], profile)

        # 2. Optional normalization
        check_cancel()
        if spec["normalize"]:
            data = normalize_mod.normalize_blocks(
                data, model=spec["normalize_model"], progress=prog,
                cache=manifest["blocks"])
            with open(classified, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        # tts.generate_audiobook reads `classified` back from disk rather than
        # the in-memory `data` list, so the _cache_hash tags (and any
        # normalization above) must be persisted before that call regardless
        # of whether the normalize branch above already did its own dump
        # (real bug hit 2026-08-29: with normalize=off, _cache_hash never
        # reached disk, so every run looked like an all-cache-miss first run).
        with open(classified, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 3. Synthesize -- cache hits (unchanged blocks) skip Kokoro entirely
        doc_cache_dir = audio_cache.cache_dir(JOBS_DIR, spec["doc_id"])
        _, new_entries = tts.generate_audiobook(
            input_json=classified, temp_folder=os.path.join(jd, "temp"),
            voice=spec["voice"], lang=spec["lang"], speed=spec["speed"],
            timeline_path=timeline, progress=prog, clean=True,
            cache=manifest["blocks"], cache_dir=doc_cache_dir,
            google_voice=spec.get("google_voice"),
            # Live per-block manifest written during synthesis (2026-09-05) --
            # feeds /manifest + /block so the progressive player can start
            # streaming before the join/synced phases have run.
            progressive_path=os.path.join(jd, "temp", "blocks.json"))

        # Persist the cache: keep every block this run actually used (reused
        # or freshly synthesized), drop anything else -- a section deleted
        # from the document, or a leftover from a different settings profile
        # (standing instruction: always delete leftovers, don't let a
        # per-document cache grow unbounded across edits).
        used_hashes = {b["_cache_hash"] for b in data if b.get("_cache_hash")}
        final_blocks = {h: manifest["blocks"][h] for h in used_hashes
                        if h in manifest["blocks"]}
        cache_hits = len(final_blocks)
        final_blocks.update(new_entries)
        audio_cache.save_manifest(JOBS_DIR, spec["doc_id"], profile, final_blocks)
        _set(job_id, cache_hits=cache_hits, cache_misses=len(new_entries),
             doc_id=spec["doc_id"])

        # 4. Join
        check_cancel()
        _set(job_id, phase="join", pct=99)
        join_audios.join(temp_folder=os.path.join(jd, "temp"), output=audio,
                         list_file=os.path.join(jd, "file_list.txt"))

        # 5. Synced + cues
        check_cancel()
        _set(job_id, phase="synced")
        # embed=True: audio inlined as a base64 data URI, not a relative
        # <audio src="audiobook.mp3">. Without this, synced.html only plays
        # when audiobook.mp3 sits right next to it under that exact name --
        # renaming/moving either file independently (e.g. copying outputs
        # into a differently-named project folder) silently breaks playback
        # with no error, just a dead <audio> tag (real bug hit 2026-08-28).
        # Costs ~33% larger HTML (base64 overhead); worth it for a file
        # that's meant to be copied elsewhere as a standalone deliverable.
        make_synced.build(timeline_path=timeline, audio_file=audio,
                           output_html=synced, title=spec["title"], embed=True)
        make_synced.write_cues(timeline_path=timeline,
                                vtt_path=os.path.join(jd, "subtitles.vtt"),
                                lrc_path=os.path.join(jd, "subtitles.lrc"))

        _set(job_id, status="done", phase="done", pct=100)
    except JobCancelled:
        _set(job_id, status="cancelled", phase="cancelled")
    except Exception as e:
        traceback.print_exc()
        _set(job_id, status="error", error=f"{type(e).__name__}: {e}")
    finally:
        cancel_events.pop(job_id, None)


def _worker():
    while True:
        job_id, spec = work_q.get()
        # A queued (not yet started) job can be cancelled before its turn --
        # skip running it entirely rather than starting work just to abort
        # on the first check_cancel() call.
        ev = cancel_events.get(job_id)
        if ev and ev.is_set():
            _set(job_id, status="cancelled", phase="cancelled")
            cancel_events.pop(job_id, None)
        else:
            _run_job(job_id, spec)
        work_q.task_done()


threading.Thread(target=_worker, daemon=True).start()


# --- Transcribe jobs (2026-09-09) ---------------------------------------------
# A SECOND queue + worker: transcription of a long capture runs tens of
# minutes on CPU and must never starve (or be starved by) audiobook jobs.
# Both queues share the jobs dict / status persistence / cancel machinery.

def _run_transcribe_job(job_id, spec):
    jd = os.path.join(JOBS_DIR, job_id)
    cancel_ev = cancel_events[job_id]

    def check_cancel():
        if cancel_ev.is_set():
            raise JobCancelled()

    def prog(cur, total, phase):
        check_cancel()
        _set(job_id, phase=phase, current=cur, total=total,
             pct=round(cur / total * 100) if total else 0)

    try:
        _set(job_id, status="running", phase="extract-audio", current=0, total=0, pct=0)
        summary = stt_pipeline.run(jd, spec, prog, check_cancel)
        _set(job_id, status="done", phase="done", pct=100, **summary)
    except JobCancelled:
        _set(job_id, status="cancelled", phase="cancelled")
    except Exception as e:
        traceback.print_exc()
        _set(job_id, status="error", error=f"{type(e).__name__}: {e}")
    finally:
        cancel_events.pop(job_id, None)


def _stt_worker():
    while True:
        job_id, spec = stt_q.get()
        ev = cancel_events.get(job_id)
        if ev and ev.is_set():
            _set(job_id, status="cancelled", phase="cancelled")
            cancel_events.pop(job_id, None)
        else:
            _run_transcribe_job(job_id, spec)
        stt_q.task_done()


stt_q = queue.Queue()
threading.Thread(target=_stt_worker, daemon=True).start()


@app.route("/convert", methods=["POST"])
def convert():
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "no file")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED:
        abort(400, f"unsupported type {ext}")

    job_id = uuid.uuid4().hex[:12]
    jd = os.path.join(JOBS_DIR, job_id)
    os.makedirs(jd, exist_ok=True)
    path = os.path.join(jd, "input" + ext)
    f.save(path)

    title = os.path.splitext(os.path.basename(f.filename))[0]
    # doc_id ties this run to any earlier run of "the same document" for
    # incremental re-conversion (2026-08-29): defaults to the filename so
    # re-uploading "Report.docx" after adding a section just works, but a
    # caller can pass an explicit one if the filename isn't stable (e.g. a
    # skill exporting to a fresh temp path each time).
    doc_id = request.form.get("doc_id") or audio_cache.sanitize_doc_id(title)
    spec = {
        "path": path,
        "voice": request.form.get("voice", "af_heart"),
        # "auto" (default since 2026-09-02): per-chunk routing -- mostly-Greek
        # script goes to Google Cloud TTS (el-GR-Wavenet-B), everything else
        # to Kokoro. "el" forces Google for all chunks; a/b/e/f/i/p/h/j/z stay
        # pure Kokoro exactly as before.
        "lang": request.form.get("lang", "auto"),
        # Optional Google voice override (e.g. el-GR-Chirp3-HD-Kore). Defaults
        # to GOOGLE_TTS_VOICE in the container (el-GR-Wavenet-B, cheapest tier).
        "google_voice": request.form.get("google_voice") or None,
        "speed": float(request.form.get("speed", "1") or 1),
        # Defaults to on (2026-08-28) -- narration quality matters more than
        # raw speed for this pipeline's actual use, and the only caller now
        # is the skill, not a UI checkbox a user might forget to tick.
        # Still overridable (normalize=off) for a fast raw pass if wanted.
        "normalize": request.form.get("normalize", "on") != "off",
        "normalize_model": request.form.get("normalize_model", "qwen3:8b"),
        "title": title,
        "doc_id": doc_id,
    }
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "phase": "queued", "pct": 0,
                        "current": 0, "total": 0, "filename": f.filename,
                        "queue_pos": work_q.qsize()}
    _set(job_id)  # no-op update, but persists the freshly-created entry to disk
    cancel_events[job_id] = threading.Event()
    work_q.put((job_id, spec))
    return jsonify({"job_id": job_id})


@app.route("/transcribe", methods=["POST"])
def transcribe_job():
    """Recording -> personalized transcript + notes (stt_pipeline). Same job
    machinery as /convert: poll /status/<job_id>, deliver from /files/. The
    original audio + transcript + notes are staged into archive_pending/ for
    the audio-memories flush."""
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "no file")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in stt_pipeline.AUDIO_EXTS:
        abort(400, f"unsupported audio type {ext}")

    job_id = uuid.uuid4().hex[:12]
    jd = os.path.join(JOBS_DIR, job_id)
    os.makedirs(jd, exist_ok=True)
    path = os.path.join(jd, "input" + ext)
    f.save(path)

    spec = {
        "path": path,
        # auto = Whisper's own detection (el/en first-class per design);
        # force with language=el / language=en
        "language": request.form.get("language", "auto"),
        # large-v3-turbo for pure English; large-v3 for Greek / code-switching
        "asr_model": request.form.get("asr_model", stt_pipeline.DEFAULT_ASR_MODEL),
        "notes_model": request.form.get("notes_model", "qwen3:8b"),
        "title": request.form.get("title")
                 or os.path.splitext(os.path.basename(f.filename))[0],
    }
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "phase": "queued", "pct": 0,
                        "current": 0, "total": 0, "filename": f.filename,
                        "kind": "transcribe", "queue_pos": stt_q.qsize()}
    _set(job_id)  # persists the freshly-created entry
    cancel_events[job_id] = threading.Event()
    stt_q.put((job_id, spec))
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            abort(404)
        return jsonify(dict(j, job_id=job_id))


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            abort(404, "job not found")
        if j["status"] not in ("queued", "running"):
            return jsonify(ok=True, note=f"job already {j['status']}")
    ev = cancel_events.get(job_id)
    if ev:
        ev.set()
    # Not set to "cancelled" here -- the worker/prog() checkpoint does that
    # once it actually stops, so the UI reflects real state, not intent.
    _set(job_id, status="cancelling")
    return jsonify(ok=True)


@app.route("/files/<job_id>/<path:name>")
def files(job_id, name):
    return send_from_directory(os.path.join(JOBS_DIR, job_id), name)


# --- Progressive (streaming) playback, added 2026-09-05 ----------------------
# Synthesis writes temp/blocks.json atomically per completed block (see
# tts.generate_audiobook). These three endpoints turn it into a start-now,
# read-along experience: /manifest is polled by the player, /block serves each
# finished wav, /progressive is the player page itself. Works after the job is
# done too (all entries present immediately), so it's also just a lighter
# alternative to synced.html when nothing needs embedding.

@app.route("/manifest/<job_id>")
def manifest(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            abort(404, "job not found")
        meta = {k: j.get(k) for k in
                ("status", "phase", "current", "total", "filename",
                 "doc_id", "error", "cache_hits", "cache_misses")}
        # jobs dict's "blocks" is a narrated-BLOCK COUNT (set post-extract);
        # the manifest's "blocks" is the list of them. Rename to avoid the
        # collision so clients can tell the two apart.
        meta["narrated"] = j.get("blocks")
    out = {"job_id": job_id, **meta,
           "sample_rate": None, "blocks": []}
    path = os.path.join(JOBS_DIR, job_id, "temp", "blocks.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            out["sample_rate"] = data.get("sample_rate")
            out["blocks"] = data.get("blocks", [])
        except (OSError, json.JSONDecodeError):
            pass  # torn read impossible (atomic replace) but never 500 on it
    return jsonify(out)


@app.route("/block/<job_id>/<int:idx>")
def block_wav(job_id, idx):
    if job_id not in jobs:
        abort(404, "job not found")
    import subprocess
    src = os.path.join(JOBS_DIR, job_id, "temp", f"block_{idx}.wav")
    if not os.path.isfile(src):
        abort(404, "block not ready")
    # The wavs tts.py writes are float32 (soundfile's default) -- Chromium
    # decodes those, but other engines can reject or (worse) decode to an
    # empty buffer, which the player then "plays" as silence. PCM16 decodes
    # everywhere; transcoding a few-MB block costs ffmpeg milliseconds
    # (found the hard way 2026-09-05: a silent-playback session where
    # everything looked healthy server-side).
    try:
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", src,
             "-f", "wav", "-acodec", "pcm_s16le", "-"],
            capture_output=True, check=True, timeout=30).stdout
    except subprocess.CalledProcessError as e:
        abort(500, f"ffmpeg transcode failed: {e.stderr[:200]!r}")
    return Response(pcm, mimetype="audio/wav")


_PLAYER_TMPL = {"html": None}  # cached read of templates/progressive_player.html


@app.route("/progressive/<job_id>")
def progressive(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            abort(404, "job not found")
        title = j.get("filename") or job_id
    if _PLAYER_TMPL["html"] is None:
        with open(os.path.join(APP_DIR, "templates", "progressive_player.html"),
                  "r", encoding="utf-8") as f:
            _PLAYER_TMPL["html"] = f.read()
    return _PLAYER_TMPL["html"].replace("%%JOB_ID%%", job_id) \
                               .replace("%%TITLE%%", str(escape(title)))




if __name__ == "__main__":
    print("🌐 lexicast API -> http://0.0.0.0:5005")
    # 0.0.0.0 directly in source now (was 127.0.0.1, patched at Docker build
    # time via sed): backend-only, always runs in the container, no more
    # "bare local browser use" case that wanted the safer 127.0.0.1 default.
    app.run(host="0.0.0.0", port=5005, threaded=True)
