# Architecture & module map

lexicast is a set of small, single-purpose Python modules. Each runs standalone
(`python <module>.py ...`) and is also imported by the orchestrators (`main.py`,
`session2notes.py`, `webapp.py`).

## Documents → audio

| Module | Role | In → Out |
|--------|------|----------|
| `extract_text.py` | PDF text + font sizes (PyMuPDF), drops tables/page numbers | `*.pdf` → `vision_output.json` |
| `classify.py` | Jenks natural-breaks on font size → header/body/caption/other | `vision_output.json` → `classified_text.json` |
| `extract_markdown.py` | `.tex` via **pandoc** → Markdown → clean labelled blocks; math made speakable | `*.tex/.md` → `classified_text.json` |
| `normalize.py` | *optional* LLM text normalization (numbers/abbreviations) via Ollama | blocks → blocks |
| `tts.py` | **Kokoro** synthesis; per-block WAVs + global `timeline.json` | `classified_text.json` → `temp/block_*.wav` + `timeline.json` |
| `join_audios.py` | ffmpeg concat of block WAVs | `temp/*.wav` → `audiobook.mp3` |
| `make_synced.py` | self-contained synced HTML + WebVTT/LRC cue export | `timeline.json` + audio → `synced.html`, `subtitles.{vtt,lrc}` |
| `main.py` | orchestrator CLI for the whole documents→audio flow | a file → audio + synced |
| `webapp.py` | Flask drag-and-drop UI wrapping `main.py`'s steps with live progress | browser → job folder |

## Recording → insight

| Module | Role | In → Out |
|--------|------|----------|
| `extract_audio.py` | ffmpeg → 16 kHz mono WAV | media → `audio.16k.wav` |
| `transcribe.py` | Whisper ASR; backend **mlx** (Apple GPU) or **faster-whisper** (CPU/CUDA) | WAV → segments + word timestamps |
| `diarize.py` | **pyannote** speaker turns → assign speakers to segments | WAV + segments → labelled segments |
| `extract_notes.py` | **Ollama** structured notes (summary, recap, takeaways, decisions, actions, topics, Q&A); map-reduce for long transcripts | transcript text → `notes.json` + `notes.md` |
| `make_latex.py` | fills `templates/session_report_template.tex` (minutes + transcript); merges speaker turns; escapes Unicode | notes + segments → `session_report.tex` |
| `make_handout.py` | LLM-generated participant handout (topics + "why it matters") → fills `handout_template.tex` | `notes.json` → `handout.{tex,json}` |
| `enrich_handout.py` | adds per-session `references.json` links, SOTA, and `alignment_section.tex` → `handout_v2` | handout + session data → `handout_v2.{tex,pdf}` |
| `compile_pdf.py` | `.tex` → PDF via tectonic → Docker `texlive` → pdflatex | `*.tex` → `*.pdf` |
| `rag.py` | local RAG: Ollama embeddings + cosine retrieval + LLM answer with citations | session dir / `.tex` / text → `index` → answers |
| `session2notes.py` | orchestrator CLI for the whole recording→insight flow | media → a session folder |

## Shared conventions

- **`classified_text.json`** — `[{text, label, ...}]` is the common intermediate for the audio pipeline (`label` ∈ header/body/caption/other; `other` is skipped).
- **`timeline.json`** — `[{index, start_ms, end_ms, text, label, section}]` powers synced + cue files; the synced transcript player reuses it with `section = speaker`.
- **`temp/`** — per-block WAVs; `tts.py` wipes stale ones at the start of each run so an old run can't leak into the join.
- A **session folder** (`<name>_session/`) collects all recording outputs and the RAG index in one place.
