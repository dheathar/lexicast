# lexicast

**Local, two-way documents ⇄ audio — plus session intelligence.** No API keys, nothing leaves your machine.

lexicast does two complementary things, fully offline:

1. **Documents → Audiobook + Synced Transcript** — turn a PDF, LaTeX (`.tex`) or Markdown file into a narrated MP3 with a self-contained, synced transcript web page (highlight follows the audio, click-to-seek, speed controls).
2. **Recording → Transcript + Notes + Reports + Q&A** — turn an audio/video recording into a speaker-labeled transcript, structured notes, polished **LaTeX/PDF** reports (meeting minutes *and* a participant handout), a synced transcript player, and a local **RAG** index you can ask questions against.

Everything runs locally: [Kokoro](https://github.com/hexgrad/kokoro) (TTS), [Whisper](https://github.com/openai/whisper) via [MLX](https://github.com/ml-explore/mlx) or [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (ASR), [pyannote.audio](https://github.com/pyannote/pyannote-audio) (diarization), and [Ollama](https://ollama.com) (LLM + embeddings).

> Origin: started as a build of [Estikno/PdfToAudiobook](https://github.com/Estikno/PdfToAudiobook) and grew into a full local media toolkit.

---

## What you get

| From | To |
|------|----|
| `book.pdf` / `paper.tex` / `notes.md` | `audiobook.mp3` + `synced.html` (+ WebVTT/LRC cues) |
| `meeting.mp4` / `call.m4a` | `transcript.{txt,srt,vtt,json}`, `notes.{md,json}`, `session_report.pdf`, `handout.pdf`, synced `transcript.html`, RAG index |

A **drag-and-drop web UI** wraps the audiobook pipeline; the session pipeline is a CLI.

---

## Architecture

```
DOCUMENTS → AUDIO                         RECORDING → INSIGHT
─────────────────                         ───────────────────
 .pdf  ─ extract_text ─ classify ┐         video/audio ─ extract_audio (ffmpeg)
 .tex  ─ pandoc ─┐                ├─►            │
 .md   ─────────── extract_markdown          transcribe (Whisper: MLX/faster-whisper)
                  │  classified_text.json        │
                  ▼                          diarize (pyannote)  ← optional, needs HF token
            tts (Kokoro) ─ timeline.json         │
                  │                          extract_notes (Ollama, map-reduce)
            join_audios (ffmpeg) ─ mp3            │
                  │                          make_latex / make_handout / enrich_handout → .tex
            make_synced → synced.html           │   compile_pdf (tectonic | docker | pdflatex)
            + WebVTT/LRC cues                 make_synced → transcript.html
                                              rag (Ollama embeddings + LLM) → ask/chat

      webapp.py  ──────────►  browser UI for the documents→audio pipeline
```

See [`docs/architecture.md`](docs/architecture.md) for the module-by-module map.

---

## Install

**Python 3.12** is required (PyTorch/Kokoro/pyannote have no 3.13/3.14 wheels yet).

```bash
# system tools
brew install ffmpeg pandoc espeak-ng tectonic      # macOS
# (Linux: apt install ffmpeg pandoc espeak-ng; install tectonic or texlive; or use Docker for PDFs)

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional, for specific features:
- **Speaker diarization** → a free Hugging Face token (see [`docs/diarization.md`](docs/diarization.md)).
- **Notes / RAG / text-normalization** → [Ollama](https://ollama.com) running locally (`ollama serve`) with a chat model (e.g. `qwen3:8b` or `gemma`) and an embedding model (`nomic-embed-text` or `bge-m3`).
- **PDF compile** → `tectonic` (single binary) **or** Docker (`texlive/texlive`) **or** a `pdflatex` install.

Platform note: on **Apple Silicon**, `mlx-whisper` installs automatically and runs Whisper on the GPU. On **Windows/Linux** it's skipped and `faster-whisper` (CPU, or CUDA) is used — see [`docs/install.md`](docs/install.md).

---

## Quick start

### Documents → audiobook + synced transcript
```bash
.venv/bin/python main.py book.pdf
.venv/bin/python main.py paper.tex --voice am_michael
.venv/bin/python main.py notes.md  --embed          # single self-contained synced.html
# then open synced.html
```
Or the web UI:
```bash
.venv/bin/python webapp.py          # → http://localhost:5005  (drag a file, pick options, download)
```

### Recording → transcript + notes + reports + Q&A
```bash
.venv/bin/python session2notes.py meeting.mp4 --pdf --rag           # full pipeline
.venv/bin/python session2notes.py call.m4a --no-diarize --asr-model large-v3-turbo
.venv/bin/python make_handout.py meeting_session --title "Workshop" # participant handout PDF
.venv/bin/python rag.py chat meeting_session/                       # ask questions about it
```

Full flag reference and recipes: [`docs/audiobook.md`](docs/audiobook.md) and [`docs/sessions.md`](docs/sessions.md).

---

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — modules and data flow
- [`docs/install.md`](docs/install.md) — setup, platforms, troubleshooting
- [`docs/audiobook.md`](docs/audiobook.md) — documents→audio pipeline + web UI + synced/cues
- [`docs/sessions.md`](docs/sessions.md) — recording→transcript/notes/reports/RAG
- [`docs/diarization.md`](docs/diarization.md) — Hugging Face token & speaker labels
- [`docs/reports.md`](docs/reports.md) — LaTeX templates, handout, enrichment, PDF compile

---

## Design notes

- **Models do what they're good at.** Whisper transcribes (there's no speech-to-text in Ollama); Kokoro narrates; pyannote diarizes; the LLM only *understands* (notes, RAG, optional text normalization). Phonemization is handled by misaki/espeak-ng — never an LLM.
- **Synced sync is exact** — timings come straight from each synthesized clip's duration, so no alignment model is needed.
- **The report tooling is generic.** Templates render whatever per-session data exists (`references.json`, `alignment_section.tex`); nothing topic-specific is baked into the code.
- **Privacy:** everything runs on-device. Recordings, transcripts and notes never leave the machine.

## Licence

MIT — see [`LICENSE`](LICENSE).
