# Install & platforms

## Requirements

- **Python 3.12** (PyTorch / Kokoro / pyannote / mlx have no 3.13/3.14 wheels yet).
- **System tools:** `ffmpeg` (always), `pandoc` (for `.tex` input), `espeak-ng` (Kokoro G2P fallback / non-English), a LaTeX engine (`tectonic`, Docker `texlive`, or `pdflatex`) for PDFs.
- **Ollama** (optional) for notes, RAG, and text normalization.
- **Hugging Face token** (optional) for speaker diarization — see [diarization.md](diarization.md).

```bash
# macOS
brew install ffmpeg pandoc espeak-ng tectonic
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
# Debian/Ubuntu
sudo apt install -y ffmpeg pandoc espeak-ng
# tectonic: see https://tectonic-typesetting.github.io , or use Docker for PDFs, or apt install texlive-full
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Speech-to-text backend by platform

`requirements.txt` gates `mlx-whisper` to Apple Silicon via an environment marker:

```
mlx-whisper ; sys_platform == "darwin" and platform_machine == "arm64"
```

- **macOS (Apple Silicon):** `mlx-whisper` installs and runs Whisper on the GPU — fast. Default engine `auto` selects it. Diarization also defaults to the GPU (`--device auto` → MPS).
- **Windows / Linux:** `mlx-whisper` is skipped; `faster-whisper` (CPU) is used. For NVIDIA GPUs: `--asr-engine faster-whisper --device cuda` (install CUDA-enabled `ctranslate2` + cuDNN per faster-whisper docs).

Pick a model with `--asr-model` (`tiny`…`large-v3`, `large-v3-turbo`, `distil-large-v3`). On Apple Silicon, `large-v3-turbo` transcribes ~15× real-time.

## First run downloads

- Kokoro downloads its ~few-hundred-MB model on first synthesis.
- Whisper downloads the chosen model (turbo ≈ 1.6 GB) on first transcription.
- pyannote downloads gated models after you accept their terms (see diarization.md).

## Troubleshooting

- **`pandoc not found`** → install pandoc (only needed for `.tex`).
- **PDF won't compile** → install `tectonic`, or run `--pdf-engine docker` (uses `texlive/texlive`), or install `pdflatex`.
- **`Could not reach Ollama`** → `ollama serve` and pull a model (`ollama pull qwen3:8b`, `ollama pull nomic-embed-text`).
- **Diarization 403 / gated** → accept the model terms on Hugging Face and `hf auth login` (diarization.md).
- **`objc[...] Class AVF... implemented in both`** warning → harmless (PyAV vs system ffmpeg dylib); ignore.
- **Missing-character / font warnings in PDF** → Unicode punctuation in text; `make_latex.esc()` maps common cases (em/en dash, curly quotes, ellipsis). Report any that slip through.
