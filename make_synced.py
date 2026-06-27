"""Build a self-contained synced HTML page from timeline.json + the audio file.

Each timeline segment becomes a clickable span. As the <audio> plays, the active
segment (the one whose [start_ms, end_ms) contains the current time) is highlighted
and scrolled into view. Click any line to seek there. Header segments are styled
as headings. No external assets — pure inline CSS/JS — so it opens straight from disk.

By default the audio is referenced relatively (small HTML, keep the two files
together). Use embed=True to inline the audio as a data URI (one portable file).
"""

import base64
import json
import os

TIMELINE_JSON = "timeline.json"
AUDIO_FILE = "audiobook.mp3"
OUTPUT_HTML = "synced.html"

_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav"}


def _html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _ts_vtt(ms):
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _ts_lrc(ms):
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"[{m:02d}:{s:02d}.{ms // 10:02d}]"


def write_cues(timeline_path=TIMELINE_JSON, vtt_path="subtitles.vtt", lrc_path="subtitles.lrc"):
    """Emit standard WebVTT + LRC cue files from the timeline.

    These plug into any player/library that speaks those formats (vtt.js, an
    <audio><track>, lrc-kit, foobar2000, mpv, ...), independent of our HTML page.
    """
    with open(timeline_path, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in timeline:
            f.write(f"{seg['index'] + 1}\n")
            f.write(f"{_ts_vtt(seg['start_ms'])} --> {_ts_vtt(seg['end_ms'])}\n")
            f.write(seg["text"].replace("\n", " ") + "\n\n")

    with open(lrc_path, "w", encoding="utf-8") as f:
        for seg in timeline:
            f.write(f"{_ts_lrc(seg['start_ms'])}{seg['text']}\n")

    print(f"✅ Cue files: {vtt_path}, {lrc_path}")
    return vtt_path, lrc_path


def build(timeline_path=TIMELINE_JSON, audio_file=AUDIO_FILE,
          output_html=OUTPUT_HTML, title="Audiobook", embed=False):
    with open(timeline_path, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    if embed:
        ext = os.path.splitext(audio_file)[1].lower()
        with open(audio_file, "rb") as af:
            b64 = base64.b64encode(af.read()).decode("ascii")
        audio_src = f"data:{_MIME.get(ext, 'audio/mpeg')};base64,{b64}"
    else:
        audio_src = os.path.basename(audio_file)

    # Render segments, opening a section heading whenever it changes.
    rows = []
    last_section = None
    for seg in timeline:
        if seg["label"] == "header":
            rows.append(
                f'<h2 class="seg header" data-i="{seg["index"]}" '
                f'data-start="{seg["start_ms"]}" data-end="{seg["end_ms"]}">'
                f'{_html_escape(seg["text"])}</h2>'
            )
            last_section = seg["text"]
        else:
            sec = seg.get("section", "")
            if sec and sec != last_section:
                rows.append(f'<div class="section-label">{_html_escape(sec)}</div>')
                last_section = sec
            rows.append(
                f'<span class="seg" data-i="{seg["index"]}" '
                f'data-start="{seg["start_ms"]}" data-end="{seg["end_ms"]}">'
                f'{_html_escape(seg["text"])} </span>'
            )
    body = "\n".join(rows)

    # Compact timeline for the JS: [start_ms, end_ms] per index.
    spans_json = json.dumps([[s["start_ms"], s["end_ms"]] for s in timeline])

    html = (_TEMPLATE
            .replace("%%TITLE%%", _html_escape(title))
            .replace("%%AUDIO%%", _html_escape(audio_src))
            .replace("%%SPANS%%", spans_json)
            .replace("%%BODY%%", body))
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Synced page: {output_html}  ({len(timeline)} segments)")
    return output_html


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>
  :root { color-scheme: light dark; --fs: 18px; }
  * { box-sizing: border-box; }
  body {
    font: var(--fs)/1.7 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
    margin: 0; background: #faf9f7; color: #222;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e6e6e6; }
    .section-label { color: #9aa3b2 !important; }
    .bar button, .bar select { background: #2a2d34; color: #e6e6e6; border-color: #3a3d44; }
  }
  header {
    position: sticky; top: 0; z-index: 10;
    backdrop-filter: blur(8px);
    background: color-mix(in srgb, Canvas 80%, transparent);
    border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
    padding: 12px 20px;
  }
  header h1 { font-size: 15px; margin: 0 0 8px; font-weight: 600; opacity: .8; }
  audio { width: 100%; }
  .bar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px;
    font-size: 13px;
  }
  .bar button, .bar select {
    font: inherit; padding: 4px 10px; border-radius: 7px; cursor: pointer;
    border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
    background: color-mix(in srgb, CanvasText 5%, transparent); color: inherit;
  }
  .bar button:hover { background: color-mix(in srgb, CanvasText 12%, transparent); }
  .bar label { display: inline-flex; align-items: center; gap: 5px; opacity: .85; }
  .bar .spacer { flex: 1; }
  .bar #clock { font-variant-numeric: tabular-nums; opacity: .7; }
  main { max-width: 760px; margin: 0 auto; padding: 28px 20px 50vh; }
  .seg { cursor: pointer; border-radius: 4px; transition: background .12s, color .12s; padding: 1px 2px; }
  .seg:hover { background: color-mix(in srgb, CanvasText 8%, transparent); }
  h2.seg { font-size: 1.35em; margin: 1.4em 0 .5em; display: block; }
  .section-label {
    font-size: .67em; text-transform: uppercase; letter-spacing: .08em;
    color: #8a8a8a; margin: 1.6em 0 .4em; font-weight: 600;
  }
  .seg.active { background: #ffd34d; color: #1a1a1a; box-shadow: 0 0 0 2px #ffd34d; }
  .seg.past { opacity: .55; }
</style>
</head>
<body>
<header>
  <h1>%%TITLE%%</h1>
  <audio id="player" controls preload="metadata" src="%%AUDIO%%"></audio>
  <div class="bar">
    <button data-skip="-10" title="Back 10s (←)">« 10s</button>
    <button data-skip="10" title="Forward 10s (→)">10s »</button>
    <label>Speed
      <select id="speed" title="Playback speed ( [ and ] )">
        <option value="0.5">0.5×</option>
        <option value="0.75">0.75×</option>
        <option value="1" selected>1×</option>
        <option value="1.25">1.25×</option>
        <option value="1.5">1.5×</option>
        <option value="1.75">1.75×</option>
        <option value="2">2×</option>
      </select>
    </label>
    <label><input type="checkbox" id="autoscroll" checked> Auto-scroll</label>
    <button id="fontminus" title="Smaller text">A−</button>
    <button id="fontplus" title="Larger text">A+</button>
    <span class="spacer"></span>
    <span id="clock">0:00 / 0:00</span>
  </div>
</header>
<main id="transcript">
%%BODY%%
</main>
<script>
  const spans = %%SPANS%%;                      // [[startMs, endMs], ...]
  const player = document.getElementById('player');
  const segEls = Array.from(document.querySelectorAll('.seg'));
  let active = -1;

  // --- click a segment to seek ---
  segEls.forEach(el => el.addEventListener('click', () => {
    player.currentTime = (+el.dataset.start) / 1000 + 0.001;
    player.play();
  }));

  // --- highlight tracking (binary search for the segment containing time t) ---
  function findSeg(t) {
    let lo = 0, hi = spans.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (t < spans[mid][0]) hi = mid - 1;
      else if (t >= spans[mid][1]) lo = mid + 1;
      else { ans = mid; break; }
    }
    return ans;
  }
  function setActive(i) {
    if (i === active) return;
    if (active >= 0 && segEls[active]) segEls[active].classList.remove('active');
    active = i;
    if (i < 0) return;
    const el = segEls[i];
    if (!el) return;
    el.classList.add('active');
    segEls.forEach((e, k) => e.classList.toggle('past', k < i));
    if (autoscroll.checked) {
      const r = el.getBoundingClientRect();
      if (r.top < 100 || r.bottom > innerHeight - 60) {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }
  }

  // --- clock ---
  const fmt = s => (s < 0 || isNaN(s)) ? '0:00'
      : Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  const clock = document.getElementById('clock');
  player.addEventListener('timeupdate', () => {
    setActive(findSeg(player.currentTime * 1000));
    clock.textContent = fmt(player.currentTime) + ' / ' + fmt(player.duration);
  });
  player.addEventListener('loadedmetadata', () => {
    clock.textContent = fmt(player.currentTime) + ' / ' + fmt(player.duration);
  });

  // --- controls ---
  const speed = document.getElementById('speed');
  const autoscroll = document.getElementById('autoscroll');
  speed.addEventListener('change', () => { player.playbackRate = +speed.value; });
  document.querySelectorAll('[data-skip]').forEach(b =>
    b.addEventListener('click', () => {
      player.currentTime = Math.max(0, Math.min(player.duration || 1e9,
        player.currentTime + (+b.dataset.skip)));
    }));
  let fs = 18;
  const setFs = d => { fs = Math.max(12, Math.min(30, fs + d));
    document.documentElement.style.setProperty('--fs', fs + 'px'); };
  document.getElementById('fontplus').addEventListener('click', () => setFs(2));
  document.getElementById('fontminus').addEventListener('click', () => setFs(-2));

  // --- keyboard shortcuts ---
  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  function bumpSpeed(dir) {
    let i = SPEEDS.indexOf(+speed.value);
    i = Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 2 : i) + dir));
    speed.value = SPEEDS[i]; player.playbackRate = SPEEDS[i];
  }
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
    if (e.code === 'Space') { e.preventDefault(); player.paused ? player.play() : player.pause(); }
    else if (e.key === 'ArrowLeft')  { player.currentTime -= 10; }
    else if (e.key === 'ArrowRight') { player.currentTime += 10; }
    else if (e.key === '[') bumpSpeed(-1);
    else if (e.key === ']') bumpSpeed(1);
  });
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
