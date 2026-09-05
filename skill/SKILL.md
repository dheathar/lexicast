---
name: lexicast
description: "Use this skill whenever the user wants a document turned into audio/narrated aloud/an audiobook. Triggers include 'read this to me', 'convert to audio', 'make an audiobook', 'narrate this', 'text to speech this document', or requests to hear a .docx/.pdf/.tex/.md file. Talks to a self-hosted lexicast service over its HTTP API (Kokoro TTS, CPU-only Docker). Produces a narrated .mp3, a self-contained synced-transcript .html (audio embedded, plays standalone), .vtt/.lrc subtitle files, and a live streaming player URL for listening while synthesis runs. Do NOT use for text-to-speech of short snippets or for audio-to-text transcription."
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

## Notes

- Incremental re-conversion: re-uploading a document with the same
  filename/`doc_id` re-narrates only changed blocks; a settings change
  (voice/lang/speed/normalize) invalidates the whole cache.
- Streaming player engines: default is a plain `<audio>` chain (max browser
  compatibility); `?engine=webaudio` for gapless. `?selftest=1` appends a
  diagnostics overlay when audio is silent — check it before assuming the
  service is broken.
- One job runs at a time; extra submissions queue.
