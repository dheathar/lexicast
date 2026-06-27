# Recording → transcript + notes + reports + RAG

Turn a meeting/interview/workshop recording into a speaker-labeled transcript,
structured notes, LaTeX/PDF reports, a synced player, and a searchable Q&A index.

## CLI

```bash
.venv/bin/python session2notes.py INPUT [options]
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--asr-model` | `small` | `tiny`…`large-v3`, `large-v3-turbo`, `distil-large-v3` |
| `--asr-engine` | `auto` | `mlx` (Apple GPU) / `faster-whisper` (CPU/CUDA) |
| `--language` | auto | force a language code (e.g. `en`, `el`) |
| `--llm` | `qwen3:8b` | Ollama model for notes |
| `--no-diarize` | off | skip speaker labelling |
| `--speakers` / `--min-speakers` / `--max-speakers` | — | constrain speaker count |
| `--device` | `auto` | `cpu`/`mps`/`cuda` (auto → MPS on Apple Silicon for diarization) |
| `--pdf` | off | also compile `session_report.tex` → PDF |
| `--pdf-engine` | `auto` | `tectonic` / `docker` / `pdflatex` |
| `--rag` | off | build a RAG index for Q&A |
| `--reuse-transcript` | off | reuse `transcript.json` (e.g. add diarization without re-transcribing) |
| `--title` / `--out-dir` | filename | report title / output folder |

## Outputs (in `<name>_session/`)

- `transcript.{txt,srt,vtt,json}` — timestamped, speaker-labeled. The `.txt` merges consecutive same-speaker fragments into readable turns.
- `notes.{md,json}` — summary, recap, key takeaways, decisions, action-items table, topics, Q&A (open/reflection prompts are separated out).
- `session_report.{tex,pdf}` — meeting-minutes report (metadata + notes + full transcript).
- `transcript.html` — synced player (reuses the synced engine; speaker shown on change).
- `audio.mp3` — extracted audio.
- `rag_index.npz` + `rag_chunks.json` — with `--rag`.

For the **participant handout** and **enriched v2** (links + SOTA + app-alignment), see [reports.md](reports.md).

## Performance

On Apple Silicon with `--asr-engine mlx --asr-model large-v3-turbo`, transcription runs ~15× real-time (a 2 h recording ≈ 8 min). Diarization runs on the GPU (`--device auto` → MPS) in a few minutes; on CPU it can take 1–3× the audio length. The notes step is several Ollama calls (map-reduce) over the transcript.

## Ask questions about a session (RAG)

```bash
.venv/bin/python rag.py index meeting_session/
.venv/bin/python rag.py ask   meeting_session/ "What did we decide about X?"
.venv/bin/python rag.py chat  meeting_session/          # interactive
```

Retrieval uses Ollama embeddings (`nomic-embed-text` / `bge-m3`); answers cite supporting timestamps/sections and say so when something isn't in the recording. `rag.py` can also index a `.tex` report, a `transcript.json`, or any text file (`--embed-model`, `--llm`, `--k`).

## Add speaker labels after the fact

If you ran `--no-diarize` (or diarization was unavailable), set up your HF token
([diarization.md](diarization.md)) and re-run **without** re-transcribing:

```bash
.venv/bin/python session2notes.py meeting.mp4 --reuse-transcript --pdf --rag --out-dir meeting_session
```
It re-extracts audio, reuses `transcript.json`, diarizes, then rebuilds notes (with speaker attribution), report, player and index.
