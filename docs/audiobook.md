# Documents → audiobook + synced transcript
Turn a **PDF / LaTeX / Markdown** file into a narrated MP3 plus a synced transcript page.

## CLI

```bash
.venv/bin/python main.py INPUT [options]
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--voice` | `af_heart` | Kokoro voice (`af_bella`, `am_michael`, `bf_emma`, `if_sara`, `pm_alex`, â¦) |
| `--lang` | `a` | language: `a`=US, `b`=UK, `e`=ES, `f`=FR, `i`=IT, `p`=PT, `h`=HI, `j`=JA, `z`=ZH |
| `--speed` | `1.0` | speech speed |
| `--out` | `audiobook.mp3` | output audio (`.mp3`/`.m4a`) |
| `--synced` | `synced.html` | output synced page |
| `--title` | filename | title shown on the synced page |
| `--embed` | off | inline the audio into the HTML (single portable file) |
| `--no-synced` | off | skip the synced page |
| `--normalize` | off | rewrite text for speech via local Ollama LLM first |
| `--normalize-model` | `qwen3:8b` | model for `--normalize` |
| `--steps` | all | subset of `extract,audio,join,synced` (e.g. re-run TTS only) |

**Voice â language must match:** voice prefixes encode language+gender (`a/b/e/f/i/p/h/j/z` + `f`/`m`), e.g. `af_heart` = American female, `if_sara` = Italian female.

## Languages

Kokoro supports 9 languages. English (`a`/`b`) is best-supported and ready out of the box; Spanish/French/Italian/Portuguese/Hindi work via espeak-ng. Japanese (`j`) needs `pip install misaki[ja]`, Mandarin (`z`) needs `misaki[zh]`.

## Synced page

`make_synced.py` produces a self-contained HTML (inline CSS/JS, no dependencies):
- highlights the current sentence, click any line to seek;
- controls: speed (0.5â2Ã, `[`/`]`), skip Â±10 s (â/â), play/pause (Space), auto-scroll toggle, font size, clock;
- dark-mode aware.

It also exports **standard cue files** so the audio works in any external player/library:
- `subtitles.vtt` (WebVTT â `<audio><track>`, vtt.js, â¦)
- `subtitles.lrc` (LRC â lyric players)

Sharing: send `synced.html` + the audio together (keep in one folder), or use `--embed` for a single self-contained file.

## Progressive streaming (start listening before synthesis finishes)

Added 2026-09-05. Synthesis writes a live per-block manifest, `temp/blocks.json`,
**atomically after every completed block** (cache hits included), with per-chunk
timings relative to the block start. Invariant: an entry appears only after its
`temp/block_<i>.wav` is fully on disk. Three webapp endpoints turn that into a
start-now, read-along experience:

| Endpoint | What it does |
|---|---|
| `GET /progressive/<job_id>` | the streaming player page (same highlight/scroll/theme UX as the synced page) |
| `GET /manifest/<job_id>` | job status + the list of finished blocks (text, durations, chunk timings); poll this |
| `GET /block/<job_id>/<i>` | one finished block's WAV |

The player polls the manifest and plays blocks back-to-back with exact sentence
highlighting (timings come from synthesis itself — same "exact sync, no
guessing" rule). Playback can begin as soon as the first blocks exist —
synthesis runs ~4× faster than realtime, so it never stalls after the initial
buffer. If you catch up to the synthesis cursor it shows *synthesizing more…*.
Opening the page after the job is done works too — it then behaves as a
lightweight alternative to `synced.html` (no embedded 45 MB base64 audio).

Engine notes (learned 2026-09-05, the hard way):
- `/block` serves PCM16 WAV (ffmpeg-transcoded from the float32 wavs tts.py
  writes) — decodes everywhere.
- The player defaults to a plain `<audio>`-element engine — the most
  compatible path. `?engine=webaudio` selects the gapless Web Audio scheduler
  instead. Reason: a browser was observed where Web Audio output is completely
  silent while media elements play fine (typically an extension hooking
  AudioContext, e.g. a volume booster/equalizer).
- `?selftest=1` on the player URL runs a diagnostics overlay over the exact
  fetch → decode → play path (also works headless:
  `msedge --headless=new --autoplay-policy=no-user-gesture-required
  --enable-logging=stderr <url>` — console lines appear on stderr).

The final `audiobook.mp3` + `synced.html` are still produced exactly as
before — streaming is purely additive and does not change the pipeline's
outputs.

## Web UI

```bash
.venv/bin/python webapp.py       # â http://localhost:5005
```
Drag a `.pdf/.tex/.md`, choose voice/language/speed and the optional Ollama normalization, watch live progress (extract â normalize â synthesize â join â synced), then open the player or download MP3/VTT/LRC. Jobs run one at a time in a background worker; each lands in its own `jobs/<id>/` folder. It's a localhost dev server â not hardened for public exposure.

## Running steps standalone

```bash
.venv/bin/python extract_text.py        # PDF â vision_output.json
.venv/bin/python classify.py            # â classified_text.json
.venv/bin/python extract_markdown.py f.tex   # .tex/.md â classified_text.json
.venv/bin/python tts.py                 # â temp/block_*.wav + timeline.json
.venv/bin/python join_audios.py         # â audiobook.mp3
.venv/bin/python make_synced.py        # â synced.html + cues
```
