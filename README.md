# lexicast

**Local, two-way documents ↔ audio, plus session intelligence.** No cloud required for the core pipeline — nothing leaves your machine.

lexicast does two complementary things:

1. **Documents → Audiobook + Synced Transcript** — turn a PDF, Word (`.docx`), LaTeX (`.tex`) or Markdown file into a narrated MP3 with a self-contained, synced transcript web page (highlight follows the audio, click-to-seek, speed controls), plus standard VTT/LRC cue files.
2. **Recording → Transcript + Notes + Reports + Q&A** — turn an audio/video recording into a speaker-labeled transcript, structured notes, LaTeX/PDF reports, and a local RAG index you can question.

Everything runs locally where possible: [Kokoro](https://github.com/hexgrad/kokoro) (TTS), [Whisper](https://github.com/openai/whisper) via [MLX](https://github.com/ml-explore/mlx) or [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (ASR), [pyannote.audio](https://github.com/pyannote/pyannote-audio) (diarization), and [Ollama](https://ollama.com) (LLM normalization + embeddings). Greek narration optionally uses Google Cloud TTS under its free tier (with a built-in character guard) because Kokoro has no Greek voice.

> Origin: started as a build of [Estikno/PdfToAudiobook](https://github.com/Estikno/PdfToAudiobook) and grew into a full local media toolkit.

---

## Documents → Audiobook

### Docker quick start (recommended)

```bash
git clone <this repo> && cd lexicast
docker compose up -d --build     # API on http://localhost:5005
```

CPU-only image; no GPU or API keys needed (Greek Google TTS is optional and off unless `el`/`auto` language routing hits Greek text).

### Using the HTTP API

```bash
# submit a document (normalize = optional LLM rewrite for speech; off here)
curl -s -X POST http://localhost:5005/convert \
  -F "file=@report.docx" -F "voice=af_heart" -F "speed=1" -F "normalize=off"
# -> {"job_id":"2fd079ea789b"}

curl -s http://localhost:5005/status/2fd079ea789b     # poll: extract → (normalize) → synthesize → join → synced → done
```

When done, the job folder (`jobs/<job_id>/`, volume-mounted) contains:

| File | What it is |
|---|---|
| `audiobook.mp3` | the narration |
| `synced.html` | self-contained read-along page (audio embedded as a data URI — one portable file) |
| `subtitles.vtt` / `subtitles.lrc` | standard cue files for any player |

**Streaming playback** — you don't have to wait for the full conversion. The moment
`/convert` returns a job id, open:

```
http://localhost:5005/progressive/<job_id>
```

The player polls `GET /manifest/<job_id>` (finished blocks + exact sentence
timings), fetches each finished block from `GET /block/<job_id>/<i>` (served as
PCM16 WAV — decodes in every browser), and plays them back-to-back with the same
sentence-highlight read-along UX as `synced.html`. Synthesis typically runs
several times faster than realtime, so playback never catches up. It also works
after the job is done, as a lightweight alternative to the embedded page.
`?engine=webaudio` switches to a gapless Web Audio scheduler;
`?selftest=1` runs a diagnostics overlay over the fetch → decode → play path
(indispensable when a browser extension silently breaks Web Audio output).

**Incremental re-conversion** — re-submitting a document reuses per-block audio
cache for every unchanged block (`doc_id` defaults to the filename), so hearing
a newly-written section of a long report costs seconds, not minutes. Changing
voice/language/speed/normalization invalidates the cache (splicing audio from
two settings would be audibly broken).

### CLI (no Docker)

```bash
python main.py book.pdf                          # or .tex / .docx / .md
python main.py paper.tex --voice am_michael      # US male voice
python main.py notes.md --normalize              # LLM rewrite-for-speech first
python main.py report.docx --steps audio,join,synced   # re-run parts
```

Voices: `af_heart`, `af_bella`, `am_michael`, `am_adam`, `bf_emma`, `bm_george`, …
Languages: `a`/`b` English, `e` Spanish, `f` French, `i` Italian, `p` Portuguese
(Kokoro); `el` forces Google Cloud TTS (Greek); `auto` routes per chunk by script.

### Using lexicast as an AI agent skill

The whole service is driven by four HTTP calls, which makes it a good fit for
AI coding agents (Claude Code, opencode, …): the agent submits the file, hands
you the streaming URL immediately, and copies the finished deliverables
wherever you want them. [`skill/SKILL.md`](skill/SKILL.md) is a ready-to-drop-in
skill file (frontmatter + endpoint reference + workflow) — copy it into your
agent's skills directory, replace the base URL, and phrases like *"read this
document to me"* start working.

### Related docs

- [`CONSTITUTION.md`](CONSTITUTION.md) — principles, environment, conventions
- [`docs/audiobook.md`](docs/audiobook.md) — documents→audio in depth (incl. streaming internals)
- [`docs/sessions.md`](docs/sessions.md) — recording→notes/reports/RAG
- [`docs/install.md`](docs/install.md), [`docs/architecture.md`](docs/architecture.md)

---

## Recording → Transcript + Notes + Reports + Q&A

```bash
python session2notes.py meeting.mp4 --pdf --rag     # transcript, notes, LaTeX/PDF, RAG index
python make_handout.py meeting_session --title "Workshop"
python rag.py chat meeting_session/                 # ask questions about the recording
```

Requires the full `requirements.txt` (transcription/diarization stack), a
Hugging Face token for pyannote, and Ollama running. See
[`docs/sessions.md`](docs/sessions.md) and [`docs/diarization.md`](docs/diarization.md).

---

## License

MIT — see [LICENSE](LICENSE).
