"""Extract a clean 16 kHz mono WAV from any audio/video file via ffmpeg.

Whisper and pyannote both expect 16 kHz mono PCM, so we normalize up front.
Works for .mp4/.mov/.mkv/.mp3/.m4a/.wav/... — anything ffmpeg can read.
"""

import os
import subprocess


def to_wav(src, out_wav):
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vn",                 # drop any video stream
        "-ac", "1",            # mono
        "-ar", "16000",        # 16 kHz
        "-c:a", "pcm_s16le",
        out_wav,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_wav


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + ".16k.wav"
    to_wav(src, out)
    print(f"✅ {out}")
