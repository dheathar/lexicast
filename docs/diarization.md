# Speaker diarization (who said what)

Diarization uses [pyannote.audio](https://github.com/pyannote/pyannote-audio). Its
pretrained pipeline is **gated** on Hugging Face, so it needs a one-time setup. If
you skip this, run `session2notes.py --no-diarize` and you'll still get a full
(un-labeled) transcript, notes, reports and RAG.

## One-time setup

1. **Create a token** (Read scope): https://hf.co/settings/tokens
2. **Accept the model terms** while logged in — click *"Agree and access repository"* and fill the short form (Company + Website) on the model your pyannote version needs:
   - **pyannote.audio 4.x** (default here): https://hf.co/pyannote/speaker-diarization-community-1
   - **pyannote.audio 3.x**: https://hf.co/pyannote/speaker-diarization-3.1 **and** https://hf.co/pyannote/segmentation-3.0
3. **Log in once** so the library picks up the token automatically:
   ```bash
   .venv/bin/hf auth login --token hf_xxx --no-add-to-git-credential
   ```
   (or `export HF_TOKEN=hf_xxx`, or pass `--hf-token hf_xxx`).

> The token grants *metadata* access immediately, but downloading model files requires the **agree form to be submitted** — accepting terms is the step people miss. Verify with:
> ```bash
> .venv/bin/python -c "from huggingface_hub import hf_hub_download as d; d('pyannote/speaker-diarization-community-1','config.yaml'); print('OK')"
> ```

## Model selection

`diarize.py` defaults to `pyannote/speaker-diarization-community-1` (for pyannote 4.x). The output object differs across versions; the code handles both (`DiarizeOutput.speaker_diarization` in 4.x vs an `Annotation` in 3.x).

## Tuning

- Speakers are auto-detected. If the count looks **over-split** (common on long, multi-party calls), constrain it: `--max-speakers N` or `--speakers N` (then `--reuse-transcript` to re-run without re-transcribing).
- Speaker labels render as `Speaker 1`, `Speaker 2`, … — map them to real names by editing `transcript.json` / `notes.json` before regenerating reports.

## Security

Treat tokens as secrets. If a token is ever exposed (e.g. pasted into a chat), **rotate it** at https://hf.co/settings/tokens.
