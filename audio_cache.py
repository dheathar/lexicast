"""Per-document, per-block cache for incremental re-conversion.

Lets a document be re-submitted after only part of it changed (the "writing a
report, want to hear a newly-added section without waiting for the whole
thing to resynthesize" case) without re-normalizing or re-synthesizing blocks
whose raw (pre-normalization) text is unchanged since the last run for the
same `doc_id`.

Cache key = sha256 of (raw block text, label, section) -- deliberately NOT
the normalized text, because normalize.py's rewrite is one Ollama call per
block and non-deterministic; hashing its output would miss the cache on
unchanged input. Hashing the raw text also means the (bigger) normalize-skip
benefit and the synthesize-skip benefit both come from the same lookup.

A voice/lang/speed/normalize-model change invalidates the WHOLE cache for a
doc_id (see `load_manifest`), not just the blocks synthesized under the old
settings -- splicing audio made with two different voices/speeds into one
file is audibly broken, so partial reuse across a settings change is
deliberately not supported.

Layout: jobs/_cache/<doc_id>/manifest.json + jobs/_cache/<doc_id>/<hash>.wav
"""
from __future__ import annotations

import hashlib
import json
import os

CACHE_ROOT_NAME = "_cache"


def cache_dir(jobs_dir: str, doc_id: str) -> str:
    return os.path.join(jobs_dir, CACHE_ROOT_NAME, doc_id)


def sanitize_doc_id(name: str) -> str:
    """Filesystem-safe cache key derived from the uploaded filename (sans
    extension) -- so re-uploading "Report.docx" a second time automatically
    lands on the same cache with no extra parameter needed from the caller."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in name.strip()]
    out = "".join(keep).strip("_") or "doc"
    return out[:80]


def block_hash(text: str, label: str, section: str) -> str:
    key = f"{text.strip()}\x00{label or ''}\x00{section or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def load_manifest(jobs_dir: str, doc_id: str, profile: dict) -> dict:
    """Returns the stored manifest only if its `profile` (voice/lang/speed/
    normalize settings) matches exactly; otherwise an empty manifest, so a
    settings change starts this doc_id's cache over rather than mixing
    audio synthesized under two different profiles into one file."""
    empty = {"profile": profile, "blocks": {}}
    path = os.path.join(cache_dir(jobs_dir, doc_id), "manifest.json")
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return empty
    if data.get("profile") != profile:
        return empty
    return data


def save_manifest(jobs_dir: str, doc_id: str, profile: dict, blocks: dict) -> None:
    """Writes manifest.json and deletes any cached .wav not referenced by
    `blocks` -- e.g. a section deleted from the document, or a stale entry
    left behind by a settings change. Keeps this cache from growing
    unbounded across repeated edits of the same document (standing
    instruction: always delete leftovers)."""
    d = cache_dir(jobs_dir, doc_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"profile": profile, "blocks": blocks}, f, ensure_ascii=False, indent=2)
    keep = {entry["wav"] for entry in blocks.values()}
    for name in os.listdir(d):
        if name.endswith(".wav") and name not in keep:
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass


def cached_wav_path(jobs_dir: str, doc_id: str, entry: dict) -> str:
    return os.path.join(cache_dir(jobs_dir, doc_id), entry["wav"])
