---
name: lexicast
description: "Use this skill whenever the user wants a document turned into audio/narrated aloud/an audiobook. Triggers include 'read this to me', 'convert to audio', 'make an audiobook', 'narrate this', 'text to speech this document', or requests to hear a .docx/.pdf/.tex/.md file. Talks to a self-hosted lexicast service over its HTTP API (Kokoro TTS, CPU-only Docker). Produces a narrated .mp3, a self-contained synced-transcript .html (audio embedded, plays standalone), .vtt/.lrc subtitle files, and a live streaming player URL for listening while synthesis runs. Also transcribes recordings into a personalized transcript + notes via POST /transcribe ('capture my thinking in audio', 'transcribe this recording'). Do NOT use for text-to-speech of short snippets."
---

# lexicast — document to audiobook (agent skill)

Replace `%%BASE_URL%%` below with your deployment's base URL (e.g.
`http://localhost:5005`), then copy this file into your agent's skills
directory. Everything the agent needs is the four calls below.

## Quick reference

| Step | Call |
|---|---|
| Submit | `curl -s -X POST %%BASE_URL%%/convert -F "file=@<path>" -F "voice=af_heart" -F "speed=1" -F "normalize=off"` |
| Stream now | open `%%BASE_URL%%/progressive/<job_id>` in a browser (read-along player; starts while synthesis runs) |
| Poll | `curl -s %%BASE_URL%%/status/<job_id>` (`queued → extract → [normalize] → synthesize → join → synced → done`) |
| Cancel | `curl -s -X POST %%BASE_URL%%/cancel/<job_id>` |
| Deliverables | `jobs/<job_id>/audiobook.mp3` + `synced.html` + `subtitles.vtt/.lrc` on the host (volume-mounted) |
| Transcribe a recording | `curl -s -X POST %%BASE_URL%%/transcribe -F "file=@<audio/video>" -F "language=auto"` (personalized transcript + notes; see below) |

## Workflow

1. **Submit** the file. `normalize=on` (default) rewrites text for speech via a
   local Ollama LLM first (slower, more natural for acronym/number-heavy text);
   `normalize=off` for a fast raw pass. Voices: `af_heart`/`af_bella` (US f),
   `am_michael`/`am_adam` (US m), `bf_emma` (UK f), `bm_george` (UK m).
   Languages: `a`/`b` English, `e`/`f`/`i`/`p` Romance (Kokoro), `el` Greek
   (Google Cloud TTS, free-tier guarded), `auto` per-chunk routing.
2. **Hand the user the `/progressive/<job_id>` URL immediately** — synthesis is
   several times faster than realtime, so listening can start right away.
3. Poll `/status` until `done` (or `error` — check `.error`, full traceback in
   `docker logs`).
4. Copy the four deliverable files wherever the user wants them. Don't copy
   `classified_text.json`, `timeline.json`, `input.*`, `temp/` — intermediates.

## Transcribe — recording → personalized transcript + notes

```bash
curl -s -X POST %%BASE_URL%%/transcribe -F "file=@<audio/video>" -F "language=auto"
```

Two-pass decode: a plain Whisper pass, phonetic retrieval against the personal lexicon
(`lexicon/lexicon.db`, seeded by `lexicon_import.py` from a tb_wiki mount + Hindsight
banks), then a re-decode with the hits as faster-whisper `hotwords` (skipped when pass 1
retrieves nothing). Produces `transcript.txt/.srt/.vtt/.json`, `notes.md` (summary /
takeaways / decisions / action items with `at` deep-links into the player) and an
embedded-audio `transcript.html`; the original audio + deliverables are staged into
`archive_pending/` — mount it: an optional scheduled flush to a network drive watches
the host path. Params: `language` (`auto`|`el`|`en`|...), `asr_model` (default
`large-v3-turbo`; use `large-v3` for Greek/code-switching), `notes_model`, `title`.
Phases: `extract-audio → transcribe → lexicon → asr-pass2 → transcript → notes → synced
→ archive`. First job after a build downloads the ASR model (~1.6 GB) into the
`hf-cache` mount.

## Notes

- Incremental re-conversion: re-uploading a document with the same
  filename/`doc_id` re-narrates only changed blocks; a settings change
  (voice/lang/speed/normalize) invalidates the whole cache.
- Streaming player engines: default is a plain `<audio>` chain (max browser
  compatibility); `?engine=webaudio` for gapless. `?selftest=1` appends a
  diagnostics overlay when audio is silent — check it before assuming the
  service is broken.
- One job runs at a time; extra submissions queue.
