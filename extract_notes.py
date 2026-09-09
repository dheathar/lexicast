"""Turn a transcript into structured notes with a local Ollama LLM.

Produces a dict: {summary, decisions[], action_items[{action,owner,due}],
topics[{topic,details}], qa[{q,a}]}. Long transcripts are handled map-reduce:
extract per chunk, then consolidate. Output feeds both notes.md and the LaTeX
report. Ollama is asked for strict JSON (format=json) for reliable parsing.

The LLM does the *understanding* only — transcription is Whisper's job.
"""

import json
import re
import urllib.request

DEFAULT_MODEL = "qwen3:8b"
HOST = "http://localhost:11434"
CHUNK_CHARS = 11000          # keep well under model context
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_MAP_SYS = (
    "You analyze a meeting/session transcript chunk and extract structured notes. "
    "Return ONLY JSON with keys: title (string, 3-7 words naming this session), "
    "summary (string, 2-4 sentences), recap (string, a fuller narrative recap of "
    "what happened in this part, 1-2 paragraphs), takeaways (array of strings, the "
    "key points worth remembering), decisions (array of strings), action_items "
    "(array of {action, owner, due, at} where at copies the transcript timestamp "
    "like \"[12:34]\" shown on the source line, \"\" if none), topics (array of "
    "{topic, details} where details is 1-3 sentences), qa (array of {q, a}). Use "
    "\"\" for unknown owner/due/at. Only record decisions actually made and "
    "commitments actually given -- discussion that merely sounds like a decision "
    "is not one; unresolved points go to qa with an empty answer. Base everything "
    "strictly on the text; do not invent."
)
_REDUCE_SYS = (
    "You consolidate several partial note objects from consecutive parts of ONE "
    "session into a single final notes object. Merge duplicates, keep it coherent "
    "and ordered. Return ONLY JSON with keys: title (string, 3-7 words naming the "
    "whole session), summary (string, a tight paragraph), recap (string, a flowing "
    "narrative recap of the whole session, 2-4 paragraphs), takeaways (array of "
    "strings, the most important points), decisions (array of strings), "
    "action_items (array of {action, owner, due, at}), topics (array of {topic, "
    "details}), qa (array of {q, a})."
)

_EMPTY = {"title": "", "summary": "", "recap": "", "takeaways": [],
          "decisions": [], "action_items": [], "topics": [], "qa": []}


def _ollama_json(system, user, model):
    payload = json.dumps({
        "model": model,
        "prompt": f"{system}\n\n{user}\n\nJSON:",
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }).encode("utf-8")
    req = urllib.request.Request(f"{HOST}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        text = json.loads(resp.read())["response"]
    text = _THINK.sub("", text).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        obj = json.loads(m.group(0)) if m else dict(_EMPTY)
    for k, v in _EMPTY.items():
        obj.setdefault(k, v if not isinstance(v, list) else [])
    return obj


def _chunks(transcript_text):
    if len(transcript_text) <= CHUNK_CHARS:
        return [transcript_text]
    lines = transcript_text.splitlines(keepends=True)
    out, buf = [], ""
    for ln in lines:
        if len(buf) + len(ln) > CHUNK_CHARS and buf:
            out.append(buf)
            buf = ""
        buf += ln
    if buf:
        out.append(buf)
    return out


def extract_notes(transcript_text, model=DEFAULT_MODEL, progress=None):
    parts = _chunks(transcript_text)
    if len(parts) == 1:
        if progress:
            progress(1, 1, "notes")
        return _ollama_json(_MAP_SYS, "TRANSCRIPT:\n" + parts[0], model)

    partials = []
    for i, ch in enumerate(parts, 1):
        if progress:
            progress(i, len(parts) + 1, "notes")
        partials.append(_ollama_json(_MAP_SYS, f"TRANSCRIPT PART {i}/{len(parts)}:\n{ch}", model))

    if progress:
        progress(len(parts) + 1, len(parts) + 1, "notes")
    merged = _ollama_json(_REDUCE_SYS,
                          "PARTIAL NOTES:\n" + json.dumps(partials, ensure_ascii=False),
                          model)
    return merged


def _at_link(at):
    """'[12:34]' -> markdown link to that moment in transcript.html (the
    synced player accepts ?t=SECONDS). Non-timestamp text passes through."""
    m = re.match(r"\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?", (at or "").strip())
    if not m:
        return at or ""
    secs = 0
    for g in m.groups():
        if g is not None:
            secs = secs * 60 + int(g)
    label = f"{m.group(1)}:{m.group(2)}" + (f":{m.group(3)}" if m.group(3) else "")
    return f"[{label}](transcript.html?t={secs})"


def notes_to_markdown(notes, title="Session Notes"):
    L = [f"# {notes.get('title') or title}\n",
         "## Summary\n", (notes.get("summary") or "_None._") + "\n"]
    L.append("## Key Takeaways\n")
    L += [f"- {t}" for t in notes.get("takeaways", [])] or ["_None._"]
    L.append("\n## Recap\n")
    L.append((notes.get("recap") or "_None._") + "\n")
    L.append("## Decisions\n")
    L += [f"- {d}" for d in notes.get("decisions", [])] or ["_None._"]
    L.append("\n## Action Items\n")
    ai = notes.get("action_items", [])
    if ai:
        L.append("| # | Action | Owner | Due | At |")
        L.append("|---|--------|-------|-----|----|")
        for i, a in enumerate(ai, 1):
            at = _at_link(a.get("at", "")) or "—"
            L.append(f"| {i} | {a.get('action','')} | {a.get('owner','') or '—'} "
                     f"| {a.get('due','') or '—'} | {at} |")
    else:
        L.append("_None._")
    L.append("\n## Topics\n")
    L += [f"- **{t.get('topic','')}** — {t.get('details','')}" for t in notes.get("topics", [])] or ["_None._"]
    qa = notes.get("qa", [])
    answered = [q for q in qa if (q.get("a") or "").strip()]
    open_q = [q for q in qa if not (q.get("a") or "").strip()]
    L.append("\n## Key Q&A\n")
    L += [f"- **Q:** {q.get('q','')}\n  **A:** {q.get('a','')}" for q in answered] or ["_None._"]
    if open_q:
        L.append("\n## Open / Reflection Prompts\n")
        L += [f"- {q.get('q','')}" for q in open_q]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    txt = open(sys.argv[1], encoding="utf-8").read()
    n = extract_notes(txt)
    print(json.dumps(n, ensure_ascii=False, indent=2))
