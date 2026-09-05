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

import os

DEFAULT_MODEL = "qwen3:8b"
# OLLAMA_HOST env override lets this reach the real Ollama daemon from inside
# a container, where "localhost" means the container itself, not the host
# machine (set to http://host.docker.internal:11434 in docker-compose.yml).
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

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


def normalize_blocks(data, model=DEFAULT_MODEL, host=DEFAULT_HOST, progress=None,
                     cache=None):
    """cache: optional {block_hash: manifest_entry} from audio_cache.load_manifest
    (see webapp.py). A block already carrying a `_cache_hash` present in `cache`
    reuses that entry's `normalized_text` and skips the Ollama call entirely --
    the incremental-reconversion fast path (2026-08-29): re-submitting a
    document where only one section changed shouldn't re-normalize every
    unchanged block."""
    try:
        for i, block in enumerate(data, start=1):
            if progress:
                progress(i, len(data), "normalize")
            text = (block.get("text") or "").strip()
            if not text or block.get("label") == "other":
                continue
            h = block.get("_cache_hash")
            if cache and h and h in cache:
                block["raw_text"] = text
                block["text"] = cache[h]["normalized_text"]
                print(f"  block {i}/{len(data)}: cache hit, skipping normalize")
                continue
            print(f"  normalizing block {i}/{len(data)}...")
            new = _generate(text, model, host)
            if new:
                block["raw_text"] = text
                block["text"] = new
    except urllib.error.URLError as e:
        # A RuntimeError, not sys.exit(): normalize_blocks is a library
        # function called from webapp.py's background worker thread too, not
        # just this file's own __main__ block. sys.exit() raises SystemExit,
        # which is a BaseException, not an Exception -- it slipped straight
        # past webapp.py's `except Exception`, so the worker thread died
        # silently and the job stayed stuck at "running" forever with no
        # error ever shown to the user (real bug hit 2026-08-28).
        raise RuntimeError(f"Could not reach Ollama at {host} ({e}). Is `ollama serve` running?")
    return data


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "classified_text.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data = normalize_blocks(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Normalized -> {path}")
