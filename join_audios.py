"""Concatenate the per-block WAVs into a single audiobook file via ffmpeg.

Blocks are joined in numeric order (block_1, block_2, ...), which matches the
order the timeline was built in, so timeline.json stays valid against the output.
"""

import os
import subprocess
from pathlib import Path

TEMP_FOLDER = "temp"
OUTPUT = "audiobook.mp3"
LIST_FILE = "file_list.txt"


def join(temp_folder=TEMP_FOLDER, output=OUTPUT, list_file=LIST_FILE):
    audio_files = sorted(
        Path(temp_folder).glob("block_*.wav"),
        key=lambda x: int(x.stem.split("_")[1]),
    )
    if not audio_files:
        print("No block_*.wav files found in the temp folder.")
        return None

    with open(list_file, "w", encoding="utf-8") as f:
        for wav in audio_files:
            f.write(f"file '{wav.resolve().as_posix()}'\n")

    ext = os.path.splitext(output)[1].lower()
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if ext == ".mp3" else ["-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, *codec, output]
    subprocess.run(cmd, check=True)
    print(f"✅ Audiobook created: {output}")
    return output


if __name__ == "__main__":
    join()
