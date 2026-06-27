"""Speech-to-text with a selectable backend.

Two engines:
  * mlx           — MLX Whisper, runs on the Apple Silicon GPU. Much faster on a
                    Mac. Default on arm64 macOS.
  * faster-whisper — CTranslate2 backend, CPU (or CUDA on Windows/Linux). The
                    cross-platform fallback; the only option on Windows.

`engine="auto"` picks mlx on Apple Silicon, otherwise faster-whisper.

Returns (segments, duration_seconds, language) where each segment is
{start, end, text, words:[{start,end,word}]}. Word timestamps drive speaker
assignment and synced sync.
"""

import platform

WHISPER_DEFAULT = "small"

# Friendly size -> MLX community repo
_MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
}


def _is_apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def resolve_engine(engine):
    if engine and engine != "auto":
        return engine
    if _is_apple_silicon():
        try:
            import mlx_whisper  # noqa: F401
            return "mlx"
        except ImportError:
            pass
    return "faster-whisper"


def _audio_duration(wav_path):
    try:
        import soundfile as sf
        return sf.info(wav_path).duration
    except Exception:
        return 0.0


def _transcribe_mlx(wav_path, model_size, language):
    import mlx_whisper

    repo = _MLX_REPOS.get(model_size, model_size)  # allow a full repo id too
    result = mlx_whisper.transcribe(
        wav_path, path_or_hf_repo=repo, language=language,
        word_timestamps=True, verbose=True)  # verbose streams progress to stdout
    segments = []
    for s in result.get("segments", []):
        segments.append({
            "start": s["start"], "end": s["end"], "text": s["text"].strip(),
            "words": [{"start": w["start"], "end": w["end"], "word": w["word"]}
                      for w in s.get("words", []) if w.get("start") is not None],
        })
    return segments, _audio_duration(wav_path), result.get("language", language)


def _transcribe_faster(wav_path, model_size, language, device, compute_type, vad, progress):
    from faster_whisper import WhisperModel

    # CTranslate2 supports cpu/cuda only — there is no MPS path.
    if device in ("auto", "mps"):
        device = "cpu"
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        wav_path, language=language, word_timestamps=True, vad_filter=vad, beam_size=5)
    out = []
    for s in segments:
        out.append({
            "start": s.start, "end": s.end, "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word}
                      for w in (s.words or [])],
        })
        if progress and info.duration:
            progress(min(s.end, info.duration), info.duration, "transcribe")
    return out, info.duration, info.language


def transcribe(wav_path, model_size=WHISPER_DEFAULT, language=None, engine="auto",
               device="cpu", compute_type="int8", vad=True, progress=None):
    eng = resolve_engine(engine)
    if eng == "mlx":
        return _transcribe_mlx(wav_path, model_size, language)
    return _transcribe_faster(wav_path, model_size, language, device, compute_type, vad, progress)


if __name__ == "__main__":
    import sys
    segs, dur, lang = transcribe(sys.argv[1],
                                 model_size=sys.argv[2] if len(sys.argv) > 2 else WHISPER_DEFAULT)
    print(f"✅ {len(segs)} segments, {dur:.0f}s, language={lang}")
    for s in segs[:5]:
        print(f"  [{s['start']:.1f}-{s['end']:.1f}] {s['text'][:70]}")
