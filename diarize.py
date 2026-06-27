"""Speaker diarization ("who spoke when") with pyannote.audio.

Produces speaker turns and assigns a speaker label to each transcript segment
by maximum temporal overlap.

⚠️ The pretrained pipeline is GATED on Hugging Face. One-time setup:
  1. Create a token:  https://hf.co/settings/tokens  (and `hf auth login`)
  2. Accept model terms (click "Agree and access repository"). For pyannote.audio
     4.x (DEFAULT_MODEL below) accept:
       https://hf.co/pyannote/speaker-diarization-community-1
     For the older 3.1 pipeline instead accept:
       https://hf.co/pyannote/speaker-diarization-3.1  +  .../segmentation-3.0
  3. Or export the token:  export HF_TOKEN=hf_xxx
"""

import os

# pyannote.audio 4.x uses the "community-1" pipeline; 3.1 is for pyannote 3.x.
DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"


def get_token(explicit=None):
    tok = (explicit or os.environ.get("HF_TOKEN")
           or os.environ.get("HUGGINGFACE_TOKEN")
           or os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    if not tok:
        # fall back to a cached `huggingface-cli login` token
        try:
            from huggingface_hub import get_token as _hf_get_token
            tok = _hf_get_token()
        except Exception:
            pass
    return tok


def _resolve_device(device):
    import torch
    if device and device != "auto":
        return device
    if torch.backends.mps.is_available():   # Apple Silicon GPU — much faster
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def diarize(wav_path, token=None, model=DEFAULT_MODEL, device="auto",
            num_speakers=None, min_speakers=None, max_speakers=None):
    from pyannote.audio import Pipeline
    import torch

    tok = get_token(token)
    if not tok:
        raise RuntimeError(
            "No Hugging Face token. Diarization needs one — see diarize.py header "
            "(accept model terms + `export HF_TOKEN=...`). Or run with --no-diarize.")

    # token kwarg name changed across versions; try both.
    try:
        pipe = Pipeline.from_pretrained(model, token=tok)
    except TypeError:
        pipe = Pipeline.from_pretrained(model, use_auth_token=tok)
    if pipe is None:
        raise RuntimeError(
            f"Could not load '{model}'. Did you accept its terms on Hugging Face "
            "and is the token valid?")
    dev = _resolve_device(device)
    pipe.to(torch.device(dev))

    kw = {}
    if num_speakers:
        kw["num_speakers"] = num_speakers
    if min_speakers:
        kw["min_speakers"] = min_speakers
    if max_speakers:
        kw["max_speakers"] = max_speakers

    diar = pipe(wav_path, **kw)
    # pyannote 4.x returns a DiarizeOutput (.speaker_diarization); 3.x returns an Annotation.
    ann = getattr(diar, "speaker_diarization", diar)
    turns = [{"start": t.start, "end": t.end, "speaker": spk}
             for t, _, spk in ann.itertracks(yield_label=True)]
    return turns


def assign_speakers(segments, turns):
    """Label each transcript segment with the speaker it overlaps most."""
    # Friendly names: SPEAKER_00 -> "Speaker 1"
    labels = sorted({t["speaker"] for t in turns})
    pretty = {lab: f"Speaker {i + 1}" for i, lab in enumerate(labels)}

    for seg in segments:
        best, best_ov = None, 0.0
        for t in turns:
            ov = min(seg["end"], t["end"]) - max(seg["start"], t["start"])
            if ov > best_ov:
                best_ov, best = ov, t["speaker"]
        seg["speaker"] = pretty.get(best, "Speaker 1")
    return segments


if __name__ == "__main__":
    import sys
    turns = diarize(sys.argv[1])
    spk = sorted({t["speaker"] for t in turns})
    print(f"✅ {len(turns)} turns, {len(spk)} speakers: {spk}")
