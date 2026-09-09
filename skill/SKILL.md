---
name: lexicast
description: "Local private media pipeline on cronos, driven over HTTP. (1) Documents (.docx/.pdf/.tex/.md) -> narrated audiobook + synced transcript: 'read this to me', 'make an audiobook', 'narrate this'. (2) Recordings -> personalized transcript + notes: 'transcribe this recording', 'process my idea recording'. (3) Live capture: 'capture my thoughts about X' or 'capture an idea' ALWAYS means VOICE -- launch the recorder helper (scripts/capture.ps1) or ask for an existing audio file; it is NEVER a text interview, never 'type your thoughts', and NEVER a disk search for recordings. Kokoro TTS (+ Google TTS for Greek only), faster-whisper in; no browser UI -- this skill is the only client. Do NOT use for TTS of short snippets (a plain TTS call is cheaper)."
---

# lexicast — audiobooks, transcripts, voice captures

Local, private pipeline in Docker on cronos; every other machine uses it over Tailscale.

- **Service:** %%BASE_URL%% (container `lexicast-lexicast-1`, source `%%LEXICAST%%\`)
- **Job outputs:** `%%LEXICAST%%\jobs\<job_id>\` — volume-mounted, host-visible; never `docker cp`
- **Capture archive:** every processed recording also lands in `%%AUDIO_MEMORIES%%` (see §2)

## Behavior rules

1. **Capture/transcribe request with NO file path → ASK, never search.** Offer: (a) user
   gives the path, or (b) record now (§3). A disk hunt for recordings was tried once
   (2026-09-09): multi-script, minutes wasted, nothing gained. Don't improvise.
2. **Audiobooks: hand the user the `/progressive/<job_id>` URL immediately** after
   submitting — synthesis is faster than realtime; nobody should wait for `done` to listen.
3. **Poll `/status` every ~1.5–2s**, report phase changes only. On `error`: read the
   `.error` field, then `docker logs lexicast-lexicast-1 --tail 50` for the traceback.
4. **Deliverables only.** Never copy intermediates: `classified_text.json`, `timeline.json`,
   `input.*`, `temp/`, `jobs/_cache/`, `*_session/`, `transcript_timeline.json`.
5. **Renaming deliverables is safe** — every HTML player embeds its audio as base64.
6. **"Garbled" non-ASCII transcript in PowerShell is a display artifact** (hit with Greek,
   2026-09-09). The file is fine: read via `[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)`
   or check `transcript.json`.
7. **Cancel is cooperative** (`running → cancelling → cancelled` at the next block/segment
   boundary; a queued job skips instantly). Don't promise instant stops.
8. **Greek narration is the only cloud call in the stack** (Google TTS, free-tier-guarded).
   Fine for documents; don't loop it for experiments.

## Quick reference

| Action | Call |
|---|---|
| Service alive? | `curl -s -o /dev/null -w "%{http_code}" %%BASE_URL%%` — any numeric code (even 404) = Flask answered; connection **error** = actually down |
| Audiobook | `curl -s -X POST %%BASE_URL%%/convert -F "file=@<.docx/.pdf/.tex/.md>" -F "voice=af_heart" -F "lang=a"` |
| Transcribe a file | `curl -s -X POST %%BASE_URL%%/transcribe -F "file=@<audio/video>" -F "language=auto"` |
| Record now | `Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%%LEXICAST%%\scripts\capture.ps1','-Note','<topic>'` |
| Poll | `curl -s %%BASE_URL%%/status/<job_id>` |
| Cancel | `curl -s -X POST %%BASE_URL%%/cancel/<job_id>` |
| Listen while synthesizing | open `%%BASE_URL%%/progressive/<job_id>` in a browser |

If the service is down: Docker Desktop first (`"C:\Program Files\Docker\Docker\Docker
Desktop.exe"`, wait for `docker info`), then `docker compose up -d` in `%%LEXICAST%%`.

---

## 1) Document → audiobook

```bash
curl -s -X POST %%BASE_URL%%/convert -F "file=@<absolute path>" -F "voice=af_heart" -F "lang=a" -F "speed=1"
```

- **Types:** `.pdf .tex/.latex .docx .md/.markdown` — anything else → 400 JSON error.
- **Voices:** `af_heart`/`af_bella` (US f), `am_michael`/`am_adam` (US m), `bf_emma` (UK f),
  `bm_george` (UK m). **Languages:** `auto` (default) routes Greek-script chunks to Google
  TTS, rest to Kokoro; `el` forces Google; `a/b/e/f/i/p` = pure Kokoro. Greek voice override:
  `-F "google_voice=el-GR-Chirp3-HD-Kore"`.
- **Google free-tier guard** (chars/month, counted in `jobs/_gcp_usage.json`): 3.8M
  Wavenet/Standard, 900k Chirp3-HD — synthesis REFUSES past the cap.
- **Normalization on by default** (local Ollama `qwen3:8b` expands acronyms/numbers for
  narration; one LLM call per block = real time). `-F "normalize=off"` for a fast raw pass.
- **Incremental re-conversion:** same `doc_id` (defaults to filename) re-synthesizes only
  changed blocks; `/status` reports `cache_hits`/`cache_misses`. Changing voice/lang/speed/
  normalize invalidates the whole cache (spliced settings = audibly broken).
- **Phases:** `queued → extract → [normalize] → synthesize → join → synced → done`, with
  `current`/`total` for real progress.
- **Progressive player:** starts the moment the job id exists (polls `/manifest`, fetches
  finished blocks as PCM16). Needs one user click (autoplay policy). `?engine=webaudio` for
  gapless where Web Audio works (default `<audio>` chain is the compatibility-safe choice —
  Panos's browser breaks Web Audio). Silent playback? Append `&selftest=1`.
- **Deliverables:** `audiobook.mp3`, `synced.html` (audio embedded; Width + theme + font
  controls built in), `subtitles.vtt`, `subtitles.lrc`.

## 2) Recording → personalized transcript + notes

```bash
curl -s -X POST %%BASE_URL%%/transcribe -F "file=@<audio/video>" -F "language=auto" -F "title=optional"
```

- **Types:** `.wav .mp3 .m4a .mp4 .mov .flac .ogg .opus .webm .aac .wma .mkv .avi` (512 MB).
- **Params:** `language` `auto` (default, Whisper detection — Greek + English are the
  first-class pair) or force `el`/`en`; `asr_model` default `large-v3-turbo` — use
  `large-v3` for Greek or el↔en code-switching; `notes_model` default `qwen3:8b`.
- **Pipeline:** ASR pass 1 (plain) → phonetic retrieval against the personal lexicon
  (`lexicon/lexicon.db`) → pass 2 with hits as faster-whisper `hotwords` (skipped when pass
  1 retrieves nothing) → transcript files → notes via Ollama (`notes.md`: summary /
  takeaways / decisions / action items with `at` timestamps that deep-link to
  `transcript.html?t=SECONDS`) → `transcript.html` (audio embedded) → archive staging.
- **Phases:** `queued → extract-audio → transcribe → lexicon → asr-pass2 → transcript →
  notes → synced → archive → done`. Done-status extras: `language`, `duration_s`,
  `segments`, `hotwords` (personal terms the lexicon fed back), `title`, `archive`.
- **Timing:** CPU int8 ≈ 1–3× realtime; the FIRST job after a rebuild also downloads the
  model into the `hf-cache` mount (~1.6 GB, one-time).
- **No diarization** (pyannote deliberately not in the image) — transcripts are unlabeled.
- **Deliverables:** `transcript.txt/.srt/.vtt/.json`, `notes.md/.json`, `transcript.html`,
  `audio.mp3` — in `jobs/<job_id>/` AND auto-staged to
  `%%PENDING%%\<stamp>_<title>\`, which the scheduled task **"Lexicast audio-memories
  flush"** (every 15 min, UNC) moves to `%%AUDIO_MEMORIES%%`
  (`\\100.118.147.81\dump\audio-memories`). Share unreachable → stays pending; archiving
  NEVER fails a job.
- **The lexicon** is what makes it personal: seeded from the tb_wiki hub + Hindsight work
  banks (weekly task "Lexicast lexicon refresh", Mondays 03:15). Manual refresh:
  `docker exec lexicast-lexicast-1 python lexicon_import.py --tbwiki /tb_wiki` and
  `... --banks panos-projects,panos-world-bank,panos-dmlab,panos-ece-papel,panos-notes,harness_dev,panos-homelab`.
  Review/prune: `docker exec lexicast-lexicast-1 python lexicon.py dump-review` then
  `lexicon.py confirm <surface>` / `drop <surface>`.

## 3) Capture (record now, no file yet)

Launch the helper via **Start-Process so it opens the user's own console window** (the
agent's shell has no interactive ENTER):

```powershell
Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%%LEXICAST%%\scripts\capture.ps1','-Note','<short topic>'
```

The user talks and presses ENTER in that window; the helper records (ffmpeg dshow, prefers
a *microphone* device), auto-submits to `/transcribe`, and writes
`%%CAPTURES%%\<file>.job.txt` with the job_id. Poll for that file, then poll `/status`
normally. `captures/` is local staging only — the canonical archive happens through §2.

---

## Environment notes

- **Ollama:** the container reaches the host via `OLLAMA_HOST=http://host.docker.internal:11434`
  (set in compose). Never hardcode `localhost` inside container code.
- **CPU-only image, on purpose:** do NOT add a CUDA base image or GPU-dependent packages —
  that class of change caused repeated build failures in a past service on this machine.
- **Implementation repo:** `github.com/dheathar/lexicast-personal` (this skill's source of
  truth); `dheathar/lexicast` and `labor-innovation/lexicast` are the older upstreams.
- **Skill deployment (verified 2026-09-09):** per-harness copies at `~/.claude/skills/`,
  `~/.config/opencode/skills/`, `~/.pi/skills/`, and for agy/Gemini-CLI the interop alias
  `~/.agents/skills/` (preferred over `~/.gemini/skills/`). agy: `/skills list` to verify
  discovery, `/skills reload` to rescan without restarting. Skills load at session start —
  an already-open session runs stale text.
- **First-time setup:** clone the repo, `mkdir jobs archive_pending lexicon hf-cache`,
  `docker compose up -d --build`.
- One audiobook job runs at a time (queue); transcribe has its own queue — long
  transcriptions never block audiobooks.

## Related

- **gutenberg** — `.tex` → PDF (lexicast does `.tex` → audio, not PDF).
- `session2notes.py` — the ancestor CLI (diarization + LaTeX reports + RAG), not exposed
  as a service; the service exposes the deep path as `/transcribe` via `stt_pipeline.py`.
- `lexicon.py` / `lexicon_import.py` — the personal term store behind `/transcribe`.
