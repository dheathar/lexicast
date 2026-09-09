# lexicast — Constitution & Working Guide

A short, durable guide for anyone (human or AI assistant) picking up this project in
a future session. Read this first. For depth, see [`docs/`](docs/).

---

## 1. What this project is

lexicast is a **local, offline** toolkit that goes both ways between documents and audio,
and turns recordings into structured knowledge:

- **Documents → audiobook + synced transcript** (`main.py`, `webapp.py`)
- **Recording → transcript + notes + LaTeX/PDF reports + RAG Q&A** (`session2notes.py`, `rag.py`)

Nothing calls a cloud API. Everything runs on the user's machine.

---

## 2. Principles (do not violate)

1. **Each model does only what it's good at.**
   - Whisper transcribes. Kokoro narrates. pyannote diarizes. Ollama *understands*
     (notes, RAG, optional text normalization). **Never** use an LLM for transcription,
     narration, or phonemization (misaki/espeak-ng do G2P).
2. **The tools are generic; the content is per-session.**
   - Templates and scripts must not hardcode topic-specific material. Anything
     session-specific (curated links, an app gap-analysis) lives in per-session data
     files that *the assistant authors each time*: `references.json`, `alignment_section.tex`.
   - When asked to "add a section," add a **generic placeholder/renderer** to the tool and
     put the actual content in a session data file — never bake it into the code.
3. **Local & private.** Recordings, transcripts, and notes never leave the machine and are
   git-ignored. Don't commit them.
4. **Verify links and claims.** Web-research references before adding them; flag anything
   unconfirmed under "Worth Verifying."
5. **Exact sync, no guessing.** Audiobook highlight timings come from clip durations, not an
   alignment model. Keep it that way.

---

## 3. Environment (must-knows)

- **Python 3.12 only** (`.venv`). 3.13/3.14 have no wheels for torch/kokoro/pyannote/mlx.
- **System tools:** `ffmpeg`, `pandoc` (for `.tex`), `espeak-ng`, and a LaTeX engine
  (`tectonic`, Docker `texlive/texlive`, or `pdflatex`).
- **ASR backend** auto-selects: **MLX** (Apple-Silicon GPU, fast) vs **faster-whisper**
  (CPU/CUDA). MLX is gated to arm64 macOS in `requirements.txt`.
- **Ollama** must be running (`ollama serve`) for notes/RAG/normalization. Models seen in use:
  `qwen3:8b`, `gemma`, embeddings `nomic-embed-text` / `bge-m3`.
- **Diarization needs a Hugging Face token** + accepting gated model terms. For pyannote 4.x
  that's `pyannote/speaker-diarization-community-1`. See [`docs/diarization.md`](docs/diarization.md).
  `hf auth login` caches it; the code also reads `$HF_TOKEN`.

---

## 4. How to run

```bash
# documents → audio
.venv/bin/python main.py book.pdf                 # or .tex / .md
.venv/bin/python webapp.py                         # UI at http://localhost:5005

# recording → everything
.venv/bin/python session2notes.py meeting.mp4 --pdf --rag
.venv/bin/python make_handout.py meeting_session --title "Workshop"     # participant PDF
.venv/bin/python enrich_handout.py meeting_session --title "Workshop"   # + links/SOTA/alignment
.venv/bin/python rag.py chat meeting_session/                           # ask questions
```

Long jobs (2 h transcription, diarization) should be run in the background with progress
monitoring. Transcription streams progress; diarization is silent until done.

---

## 5. Conventions & gotchas

- **Intermediates:** `classified_text.json` (audio pipeline), `timeline.json` (sync + cues),
  `temp/blocks.json` (live per-block manifest for streaming — written atomically per block
  by `tts.py`; an entry exists only after its wav is fully on disk; keep that invariant).
  A recording's outputs all live in `<name>_session/`.
- **`temp/` is wiped** at the start of every `tts.generate_audiobook` run (`clean=True`) so a
  previous run can't leak block WAVs into the join. Keep this guard.
- **Re-diarize without re-transcribing:** `session2notes.py --reuse-transcript`.
- **LaTeX text must be escaped** through `make_latex.esc()` — it maps Unicode punctuation
  (em/en dash, curly quotes, ellipsis, bullets, arrows) to safe LaTeX. If you inject **raw**
  LaTeX (e.g. `alignment_section.tex`), use `---`/`--`, `\&`, etc. yourself — don't paste
  Unicode `—`.
- **pyannote 4.x output** is `DiarizeOutput.speaker_diarization` (an `Annotation`), not the
  bare `Annotation` of 3.x — `diarize.py` handles both.
- **Transcript readability:** Whisper often emits 1–3-word segments; `make_latex.merge_turns`
  groups consecutive same-speaker fragments. Reuse it for any transcript rendering.
- **Shell is zsh:** unquoted `$VAR` does **not** word-split; use loops or `${=VAR}`.
- **Personal lexicon (2026-09-09):** `lexicon/lexicon.db` (git-ignored, volume-
  mounted) is the personal-term store for personalized STT — the terms Whisper
  gets wrong for this speaker, each with an espeak-ng IPA so retrieval is
  phonetic. `lexicon.py` owns the store + retrieval primitive;
  `lexicon_import.py` seeds it (tb_wiki read-only mount + Hindsight banks via
  REST; `panos-home` is opt-in only, family names are PII). G2P stays espeak-ng
  in this image for BOTH import and decode so IPA forms remain comparable —
  don't switch one side's phonemizer.
- **/transcribe (2026-09-09):** POST audio/video → `stt_pipeline.py` in a
  SECOND worker queue (long transcriptions must never starve audiobook jobs):
  two-pass decode with lexicon hotword biasing (pass 2 skipped when pass 1
  retrieves nothing), notes via `extract_notes.py`, synced player, then
  best-effort staging into `archive_pending/` — which MUST stay a compose
  volume (the audio-memories flush task watches the host path; an unmounted
  path silently strands captures inside the container — hit for real on day
  one). faster-whisper (CPU int8) is in the image; pyannote diarization is
  not — transcripts are unlabeled.

---

## 6. Authoring per-session enrichment (the assistant's job)

When producing a handout/report for a session:
1. From the notes, identify named **tools, frameworks, regulations** → web-search → verified URLs.
2. Write `<session_dir>/references.json` (`regulation/tools/sota/verify/topic_links`). See
   [`docs/reports.md`](docs/reports.md) for the schema.
3. If asked how the session applies to a specific app/codebase: **audit the code**, then write
   `<session_dir>/alignment_section.tex` (raw LaTeX, e.g. a scorecard + gaps + next steps).
4. `enrich_handout.py` renders both into `handout_v2.pdf`. With neither file present it just
   rebuilds the plain handout — proving the tool stays generic.

---

## 7. Security

- Treat **HF tokens** as secrets; never commit them. If one is exposed (e.g. pasted in chat),
  **rotate it** at https://hf.co/settings/tokens.
- The web UI is a **localhost dev server** — do not expose it publicly without hardening.
- Session media/transcripts/notes are git-ignored. Keep it that way.

---

## 8. Where to look

| Need | File |
|------|------|
| Module map & data flow | [`docs/architecture.md`](docs/architecture.md) |
| Setup / platforms / troubleshooting | [`docs/install.md`](docs/install.md) |
| Documents→audio + web UI + cues | [`docs/audiobook.md`](docs/audiobook.md) |
| Recording→notes/reports/RAG | [`docs/sessions.md`](docs/sessions.md) |
| Speaker diarization & HF token | [`docs/diarization.md`](docs/diarization.md) |
| LaTeX reports, handout, enrichment | [`docs/reports.md`](docs/reports.md) |
