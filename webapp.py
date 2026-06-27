"""Local web UI for lexicast.

Drop a PDF / .tex / .md, pick a voice and options, and the server runs the
pipeline (extract -> optional Ollama normalize -> Kokoro TTS -> join -> synced)
in a background worker, streaming progress to the page. When done it links to the
synced player and the downloadable audio + cue files.

Run:  .venv/bin/python webapp.py     then open  http://localhost:5005

Single-user / localhost tool: one job runs at a time (TTS is heavy); further
submissions queue.
"""

import os
import queue
import threading
import time
import traceback
import uuid

from flask import (Flask, abort, jsonify, request,
                   send_from_directory, Response)

import extract_text
import classify
import extract_markdown
import normalize as normalize_mod
import tts
import join_audios
import make_synced

APP_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(APP_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

ALLOWED = {".pdf", ".tex", ".latex", ".md", ".markdown"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB uploads

jobs = {}                       # id -> dict(status,...)
jobs_lock = threading.Lock()
work_q = queue.Queue()


def _set(job_id, **kw):
    with jobs_lock:
        jobs[job_id].update(kw)


def _run_job(job_id, spec):
    jd = os.path.join(JOBS_DIR, job_id)
    classified = os.path.join(jd, "classified_text.json")
    timeline = os.path.join(jd, "timeline.json")
    audio = os.path.join(jd, "audiobook.mp3")
    synced = os.path.join(jd, "synced.html")
    import json

    def prog(cur, total, phase):
        _set(job_id, phase=phase, current=cur, total=total,
             pct=round(cur / total * 100) if total else 0)

    try:
        # 1. Extract
        _set(job_id, status="running", phase="extract", current=0, total=0, pct=0)
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

        # 2. Optional normalization
        if spec["normalize"]:
            data = normalize_mod.normalize_blocks(
                data, model=spec["normalize_model"], progress=prog)
            with open(classified, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        # 3. Synthesize
        tts.generate_audiobook(
            input_json=classified, temp_folder=os.path.join(jd, "temp"),
            voice=spec["voice"], lang=spec["lang"], speed=spec["speed"],
            timeline_path=timeline, progress=prog, clean=True)

        # 4. Join
        _set(job_id, phase="join", pct=99)
        join_audios.join(temp_folder=os.path.join(jd, "temp"), output=audio,
                         list_file=os.path.join(jd, "file_list.txt"))

        # 5. Synced + cues
        _set(job_id, phase="synced")
        make_synced.build(timeline_path=timeline, audio_file=audio,
                           output_html=synced, title=spec["title"])
        make_synced.write_cues(timeline_path=timeline,
                                vtt_path=os.path.join(jd, "subtitles.vtt"),
                                lrc_path=os.path.join(jd, "subtitles.lrc"))

        _set(job_id, status="done", phase="done", pct=100)
    except Exception as e:
        traceback.print_exc()
        _set(job_id, status="error", error=f"{type(e).__name__}: {e}")


def _worker():
    while True:
        job_id, spec = work_q.get()
        _run_job(job_id, spec)
        work_q.task_done()


threading.Thread(target=_worker, daemon=True).start()


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


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
    spec = {
        "path": path,
        "voice": request.form.get("voice", "af_heart"),
        "lang": request.form.get("lang", "a"),
        "speed": float(request.form.get("speed", "1") or 1),
        "normalize": request.form.get("normalize") == "on",
        "normalize_model": request.form.get("normalize_model", "qwen3:8b"),
        "title": title,
    }
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "phase": "queued", "pct": 0,
                        "current": 0, "total": 0, "filename": f.filename,
                        "queue_pos": work_q.qsize()}
    work_q.put((job_id, spec))
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            abort(404)
        return jsonify(dict(j, job_id=job_id))


@app.route("/files/<job_id>/<path:name>")
def files(job_id, name):
    return send_from_directory(os.path.join(JOBS_DIR, job_id), name)


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>lexicast — make an audiobook</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 16px/1.6 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
         margin: 0; background: #f6f7f9; color: #1c1f24;
         display: flex; min-height: 100vh; align-items: flex-start; justify-content: center; }
  @media (prefers-color-scheme: dark) { body { background: #14161a; color: #e6e6e6; }
    .card { background: #1d2026 !important; box-shadow: none !important; }
    input, select, .drop { background: #14161a !important; color: inherit; border-color: #343842 !important; } }
  .card { width: min(620px, 92vw); margin: 6vh 0; background: #fff; border-radius: 16px;
          padding: 28px 30px; box-shadow: 0 10px 40px rgba(0,0,0,.08); }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p.sub { margin: 0 0 20px; opacity: .65; font-size: 14px; }
  .drop { border: 2px dashed #c4c9d2; border-radius: 12px; padding: 26px; text-align: center;
          cursor: pointer; transition: border-color .15s, background .15s; }
  .drop.over { border-color: #f0b400; background: color-mix(in srgb, #f0b400 8%, transparent); }
  .drop b { color: #c08a00; }
  .fname { margin-top: 10px; font-size: 14px; opacity: .8; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 18px 0; }
  label.field { display: flex; flex-direction: column; gap: 5px; font-size: 13px; opacity: .85; }
  input, select { font: inherit; padding: 8px 10px; border-radius: 8px;
                  border: 1px solid #ccd2db; background: #fff; color: inherit; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 14px; margin: 4px 0 16px; }
  button.go { width: 100%; padding: 12px; border: 0; border-radius: 10px; cursor: pointer;
              font: 600 16px inherit; background: #f0b400; color: #1a1a1a; }
  button.go:disabled { opacity: .5; cursor: default; }
  .progress { display: none; margin-top: 22px; }
  .pwrap { height: 10px; background: color-mix(in srgb, CanvasText 12%, transparent);
           border-radius: 99px; overflow: hidden; }
  .pbar { height: 100%; width: 0; background: #f0b400; transition: width .3s; }
  .pmsg { font-size: 13px; margin-top: 8px; opacity: .8; }
  .result { display: none; margin-top: 22px; }
  .result a { display: inline-block; margin: 6px 10px 0 0; }
  .result .open { padding: 10px 16px; background: #f0b400; color: #1a1a1a; border-radius: 9px;
                  text-decoration: none; font-weight: 600; }
  .err { color: #c0392b; font-size: 14px; margin-top: 14px; white-space: pre-wrap; }
  audio { width: 100%; margin-top: 14px; }
</style></head>
<body>
<div class="card">
  <h1>📖 → 🎧 lexicast</h1>
  <p class="sub">Turn a PDF, LaTeX (.tex) or Markdown file into a narrated audiobook with a synced transcript.</p>

  <div class="drop" id="drop">
    <div>Drop a file here, or <b>click to browse</b></div>
    <div style="opacity:.6;font-size:13px;margin-top:4px">.pdf · .tex · .md</div>
    <div class="fname" id="fname"></div>
    <input type="file" id="file" accept=".pdf,.tex,.latex,.md,.markdown" hidden>
  </div>

  <div class="grid">
    <label class="field">Voice
      <select id="voice">
        <option value="af_heart">af_heart (US, female)</option>
        <option value="af_bella">af_bella (US, female)</option>
        <option value="am_michael">am_michael (US, male)</option>
        <option value="am_adam">am_adam (US, male)</option>
        <option value="bf_emma">bf_emma (UK, female)</option>
        <option value="bm_george">bm_george (UK, male)</option>
      </select>
    </label>
    <label class="field">Language
      <select id="lang">
        <option value="a">English (US)</option>
        <option value="b">English (UK)</option>
        <option value="e">Spanish</option>
        <option value="f">French</option>
        <option value="i">Italian</option>
        <option value="p">Portuguese</option>
      </select>
    </label>
    <label class="field">Speed
      <input id="speed" type="number" min="0.5" max="2" step="0.05" value="1">
    </label>
    <label class="field">Normalize model
      <input id="nmodel" type="text" value="qwen3:8b">
    </label>
  </div>
  <div class="row">
    <input type="checkbox" id="normalize">
    <label for="normalize">Clean text for speech with a local LLM (Ollama) — expands acronyms, numbers, units</label>
  </div>

  <button class="go" id="go" disabled>Create audiobook</button>

  <div class="progress" id="progress">
    <div class="pwrap"><div class="pbar" id="pbar"></div></div>
    <div class="pmsg" id="pmsg">Starting…</div>
  </div>
  <div class="err" id="err"></div>

  <div class="result" id="result">
    <a class="open" id="openk" target="_blank">▶ Open synced player</a>
    <audio id="preview" controls></audio>
    <div>
      <a id="dlmp3" download>⬇ Download MP3</a>
      <a id="dlvtt" download>⬇ WebVTT</a>
      <a id="dllrc" download>⬇ LRC</a>
    </div>
  </div>
</div>

<script>
  const $ = id => document.getElementById(id);
  const drop = $('drop'), fileInput = $('file');
  let chosen = null;

  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('over');
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) setFile(fileInput.files[0]); });
  function setFile(f) { chosen = f; $('fname').textContent = '📄 ' + f.name; $('go').disabled = false; }

  const PHASES = { queued:'Queued…', extract:'Extracting text…', normalize:'Normalizing with LLM',
                   synthesize:'Synthesizing speech', join:'Joining audio…', synced:'Building player…', done:'Done' };

  $('go').addEventListener('click', async () => {
    if (!chosen) return;
    $('go').disabled = true; $('err').textContent = '';
    $('result').style.display = 'none'; $('progress').style.display = 'block';
    $('pbar').style.width = '0%'; $('pmsg').textContent = 'Uploading…';

    const fd = new FormData();
    fd.append('file', chosen);
    fd.append('voice', $('voice').value);
    fd.append('lang', $('lang').value);
    fd.append('speed', $('speed').value);
    fd.append('normalize_model', $('nmodel').value);
    if ($('normalize').checked) fd.append('normalize', 'on');

    let r;
    try { r = await (await fetch('/convert', { method:'POST', body:fd })).json(); }
    catch (e) { fail('Upload failed: ' + e); return; }
    poll(r.job_id);
  });

  function fail(msg) {
    $('progress').style.display = 'none'; $('err').textContent = msg; $('go').disabled = false;
  }

  async function poll(id) {
    let s;
    try { s = await (await fetch('/status/' + id)).json(); }
    catch (e) { return setTimeout(() => poll(id), 1500); }

    if (s.status === 'error') return fail('Error: ' + s.error);

    let label = PHASES[s.phase] || s.phase;
    if ((s.phase === 'normalize' || s.phase === 'synthesize') && s.total)
      label += '  ' + s.current + '/' + s.total;
    $('pmsg').textContent = label;
    $('pbar').style.width = (s.pct || 0) + '%';

    if (s.status === 'done') {
      $('progress').style.display = 'none';
      const base = '/files/' + id + '/';
      $('openk').href = base + 'synced.html';
      $('preview').src = base + 'audiobook.mp3';
      $('dlmp3').href = base + 'audiobook.mp3';
      $('dlvtt').href = base + 'subtitles.vtt';
      $('dllrc').href = base + 'subtitles.lrc';
      $('result').style.display = 'block';
      $('go').disabled = false;
      return;
    }
    setTimeout(() => poll(id), 1200);
  }
</script>
</body></html>
"""


if __name__ == "__main__":
    print("🌐 lexicast UI -> http://localhost:5005")
    app.run(host="127.0.0.1", port=5005, threaded=True)
