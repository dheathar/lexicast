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
