"""Optional text normalization for TTS via a local Ollama LLM.

This runs *before* synthesis and rewrites each block into how it should be
*spoken*: expand abbreviations ("Dr." -> "Doctor"), numbers/dates/units
("2026" -> "twenty twenty-six", "5km" -> "five kilometers"), symbols, and fix
PDF hyphenation / line-break artifacts. It does NOT translate or summarize.

This is the legitimate place for an LLM in the pipeline — a fuzzy language task.
Phonemization (grapheme->phoneme) is deliberately NOT done here: that is a
deterministic job handled by Kokoro's misaki / espeak-ng backend.

Uses Ollama's HTTP API (no extra pip dependency). Daemon: `ollama serve`.
"""

import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_HOST = "http://localhost:11434"

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_SYSTEM = (
    "You normalize text so a text-to-speech engine reads it aloud naturally. "
    "Rewrite the user's text expanding abbreviations, acronyms, numbers, dates, "
    "currency, units and symbols into the words a narrator would actually say. "
    "Fix hyphenation and broken line breaks. Preserve meaning and wording; do NOT "
    "translate, summarize, add, or remove content. Output ONLY the rewritten text, "
    "with no preamble, quotes, or commentary."
)


def _generate(text, model, host):
    payload = json.dumps({
        "model": model,
        "prompt": f"{_SYSTEM}\n\nTEXT:\n{text}\n\nREWRITTEN:",
        "stream": False,
        "think": False,            # ignored by non-thinking models
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(f"{host}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        out = json.loads(resp.read())["response"]
    out = _THINK.sub("", out).strip()
    return out.strip().strip('"')


def normalize_blocks(data, model=DEFAULT_MODEL, host=DEFAULT_HOST, progress=None):
    try:
        for i, block in enumerate(data, start=1):
            if progress:
                progress(i, len(data), "normalize")
            text = (block.get("text") or "").strip()
            if not text or block.get("label") == "other":
                continue
            print(f"  normalizing block {i}/{len(data)}...")
            new = _generate(text, model, host)
            if new:
                block["raw_text"] = text
                block["text"] = new
    except urllib.error.URLError as e:
        sys.exit(f"❌ Could not reach Ollama at {host} ({e}). Is `ollama serve` running?")
    return data


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "classified_text.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data = normalize_blocks(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Normalized -> {path}")
