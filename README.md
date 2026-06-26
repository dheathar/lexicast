# pdf2audio — PDF / LaTeX / Markdown → audiobook + karaoke

Turn a document into an AI-narrated audiobook **locally** (no API keys), plus a
self-contained **karaoke HTML** page that highlights each sentence as it's spoken.

Based on [Estikno/PdfToAudiobook](https://github.com/Estikno/PdfToAudiobook), extended with:

- **LaTeX & Markdown input** — `.tex` is converted with `pandoc` to Markdown, then
  parsed into clean narration blocks. Because the text is already clean, this path
  **skips** the PDF font-size extraction/classification entirely.
- **Kokoro-82M TTS** — fast and near-real-time on Apple Silicon / CPU (the original
  used Coqui XTTS-v2, which is high quality but slow on CPU).
- **Karaoke page** — sentence-level highlight synced to the audio, timings taken
  directly from each synthesized clip (no alignment model). Click any line to seek.

## Pipeline

```
.pdf  → extract_text.py → classify.py (Jenks font sizes) ┐
.tex  → pandoc → extract_markdown.py (clean, structural)  ├→ classified_text.json
.md   → extract_markdown.py                               ┘
       → tts.py (Kokoro)      → temp/block_*.wav + timeline.json
       → join_audios.py       → audiobook.mp3
       → make_karaoke.py      → karaoke.html + subtitles.vtt + subtitles.lrc
```

The karaoke page is a self-contained HTML (inline CSS/JS, no dependencies) that
highlights each sentence as it plays — timings come straight from the synthesized
clip durations, so no alignment model is needed. The same timings are also exported
as standard **WebVTT** (`subtitles.vtt`) and **LRC** (`subtitles.lrc`) cue files, so
the audio works in any external player/library (`<audio><track>`, vtt.js, lrc-kit,
mpv, foobar2000, …).

## Setup

```bash
# system deps
brew install ffmpeg pandoc espeak-ng

# python 3.12 (torch/kokoro have no 3.13/3.14 wheels yet)
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
# one command, end-to-end
.venv/bin/python main.py book.pdf
.venv/bin/python main.py paper.tex --voice am_michael
.venv/bin/python main.py notes.md  --out mybook.mp3 --embed

# open karaoke.html in a browser
```

Outputs: `audiobook.mp3` and `karaoke.html` (keep them together, or use `--embed`
to inline the audio into a single portable HTML file).

### Options

| flag | default | meaning |
|------|---------|---------|
| `--voice` | `af_heart` | Kokoro voice (`af_bella`, `am_michael`, `bf_emma`, …) |
| `--lang` | `a` | language code (`a`=US, `b`=UK, `e`=ES, `f`=FR, `i`=IT, `p`=PT, `h`=HI, `j`=JA, `z`=ZH) |
| `--speed` | `1.0` | speech speed |
| `--out` | `audiobook.mp3` | output audio (`.mp3`/`.m4a`) |
| `--embed` | off | inline audio into the HTML |
| `--normalize` | off | rewrite text for speech via a local Ollama LLM before TTS |
| `--normalize-model` | `qwen3:8b` | Ollama model for `--normalize` |
| `--no-karaoke` | off | skip the karaoke page |
| `--steps` | all | run a subset: `extract,audio,join,karaoke` (e.g. to re-run TTS only) |

### Running steps individually

Each module also runs standalone with sensible defaults (`book.pdf`,
`vision_output.json`, `classified_text.json`, `temp/`, `timeline.json`):

```bash
.venv/bin/python extract_text.py        # PDF → vision_output.json
.venv/bin/python classify.py            # → classified_text.json
.venv/bin/python extract_markdown.py f.tex   # .tex/.md → classified_text.json
.venv/bin/python tts.py                 # → temp/block_*.wav + timeline.json
.venv/bin/python join_audios.py         # → audiobook.mp3
.venv/bin/python make_karaoke.py        # → karaoke.html
```

## Optional: LLM text normalization (Ollama)

`--normalize` pipes each block through a **local Ollama** model *before* synthesis,
rewriting text the way it should be *spoken* — numbers, dates, currency, units,
abbreviations and symbols — and fixing PDF hyphenation. Example:

```
$1.5M (cf. Fig. 4)  →  one point five million dollar (see Figure four)
3.4GHz vs. 2.1GHz   →  three point four gigahertz versus two point one gigahertz
```

```bash
ollama serve                          # start the daemon
.venv/bin/python main.py book.pdf --normalize --normalize-model qwen3:8b
```

This is the right job for an LLM. **Phonemization (G2P) is not** — that's
deterministic and handled by Kokoro's misaki/espeak-ng backend, so an LLM is
never used for pronunciation.

## Notes

- Synthesis is resumable at the block level: blocks are written to `temp/` as they
  finish. (Re-running currently re-synthesizes; delete `temp/` for a clean run.)
- Karaoke timings are computed against the concatenation order, so MP3 encoder
  padding adds only millisecond-level drift — imperceptible at sentence granularity.
- PDF labelling (`header`/`body`/`caption`/`other`) is heuristic; tune
  `n_classes` / breaks in `classify.py` if your PDF mis-labels. `other` is skipped.
