"""Local RAG over a session — ask a local LLM questions about the recording.

Everything runs through Ollama: an embedding model (nomic-embed-text / bge-m3)
for retrieval and a chat model (qwen3 / gemma4) for answering. No cloud, no extra
heavy deps (numpy only).

Index a session, then ask:
  .venv/bin/python rag.py index  meeting_session/
  .venv/bin/python rag.py ask    meeting_session/ "What did we decide about the launch?"
  .venv/bin/python rag.py chat   meeting_session/

Sources accepted: a session output dir (uses transcript.json + notes.json), a
.tex report (text extracted via pandoc), a transcript .json, or any text file.
Answers cite the supporting timestamps / sections and say so when the answer
isn't in the recording.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request

import numpy as np

HOST = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = "qwen3:8b"
TOP_K = 6
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# --------------------------- Ollama helpers ---------------------------------

def embed(text, model=EMBED_MODEL):
    payload = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(f"{HOST}/api/embeddings", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return np.array(json.loads(r.read())["embedding"], dtype="float32")


def chat(prompt, model=LLM_MODEL):
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "think": False, "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(f"{HOST}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return _THINK.sub("", json.loads(r.read())["response"]).strip()


# --------------------------- corpus building --------------------------------

def _hms(sec):
    sec = int(sec or 0); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _chunk_segments(segments, per=5):
    """Group consecutive transcript segments into retrievable chunks."""
    chunks = []
    for i in range(0, len(segments), per):
        grp = segments[i:i + per]
        spk = sorted({s.get("speaker", "") for s in grp if s.get("speaker")})
        text = " ".join(s["text"] for s in grp if s.get("text", "").strip())
        if not text:
            continue
        cite = f"transcript {_hms(grp[0]['start'])}–{_hms(grp[-1]['end'])}"
        if spk:
            cite += " (" + ", ".join(spk) + ")"
        chunks.append({"text": text, "cite": cite})
    return chunks


def _notes_chunks(notes):
    out = []
    if notes.get("summary"):
        out.append({"text": "Summary: " + notes["summary"], "cite": "notes/summary"})
    for d in notes.get("decisions", []):
        out.append({"text": "Decision: " + d, "cite": "notes/decision"})
    for a in notes.get("action_items", []):
        out.append({"text": f"Action item: {a.get('action','')} "
                            f"(owner: {a.get('owner','') or '?'}, due: {a.get('due','') or '?'})",
                    "cite": "notes/action"})
    for t in notes.get("topics", []):
        out.append({"text": f"Topic — {t.get('topic','')}: {t.get('details','')}", "cite": "notes/topic"})
    for q in notes.get("qa", []):
        out.append({"text": f"Q: {q.get('q','')} A: {q.get('a','')}", "cite": "notes/qa"})
    return out


def _tex_to_text(path):
    try:
        r = subprocess.run(["pandoc", path, "-f", "latex", "-t", "plain", "--wrap=none"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
    except FileNotFoundError:
        pass
    return open(path, encoding="utf-8").read()


def _text_chunks(text, size=900):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            chunks.append({"text": buf.strip(), "cite": "report"}); buf = ""
        buf += "\n" + p
    if buf.strip():
        chunks.append({"text": buf.strip(), "cite": "report"})
    return chunks


def build_corpus(path):
    if os.path.isdir(path):
        chunks = []
        tj = os.path.join(path, "transcript.json")
        nj = os.path.join(path, "notes.json")
        if os.path.exists(tj):
            chunks += _chunk_segments(json.load(open(tj, encoding="utf-8")))
        if os.path.exists(nj):
            chunks += _notes_chunks(json.load(open(nj, encoding="utf-8")))
        if not chunks:
            sys.exit(f"❌ no transcript.json/notes.json in {path}")
        return chunks
    if path.endswith((".tex", ".latex")):
        return _text_chunks(_tex_to_text(path))
    if path.endswith(".json"):
        return _chunk_segments(json.load(open(path, encoding="utf-8")))
    return _text_chunks(open(path, encoding="utf-8").read())


# ------------------------------ index I/O -----------------------------------

def _index_paths(source):
    base = source.rstrip("/") if os.path.isdir(source) else os.path.dirname(os.path.abspath(source))
    d = source if os.path.isdir(source) else base
    return os.path.join(d, "rag_index.npz"), os.path.join(d, "rag_chunks.json")


def build_index(source, embed_model=EMBED_MODEL):
    chunks = build_corpus(source)
    print(f"📚 embedding {len(chunks)} chunks with '{embed_model}'...")
    vecs = []
    for i, c in enumerate(chunks, 1):
        v = embed(c["text"], embed_model)
        vecs.append(v / (np.linalg.norm(v) + 1e-8))
        if i % 10 == 0 or i == len(chunks):
            print(f"   {i}/{len(chunks)}")
    npz, cj = _index_paths(source)
    np.savez(npz, vecs=np.vstack(vecs))
    json.dump({"chunks": chunks, "embed_model": embed_model},
              open(cj, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"✅ index -> {npz}")
    return npz, cj


def load_index(source):
    npz, cj = _index_paths(source)
    if not (os.path.exists(npz) and os.path.exists(cj)):
        return None
    data = json.load(open(cj, encoding="utf-8"))
    return {"vecs": np.load(npz)["vecs"], "chunks": data["chunks"],
            "embed_model": data.get("embed_model", EMBED_MODEL)}


def search(index, query, k=TOP_K):
    q = embed(query, index["embed_model"])
    q = q / (np.linalg.norm(q) + 1e-8)
    sims = index["vecs"] @ q
    order = np.argsort(-sims)[:k]
    return [(index["chunks"][i], float(sims[i])) for i in order]


def ask(source, question, llm=LLM_MODEL, k=TOP_K, index=None):
    index = index or load_index(source)
    if index is None:
        print("ℹ️  no index yet — building it...")
        build_index(source)
        index = load_index(source)
    hits = search(index, question, k)
    context = "\n\n".join(f"[{i+1}] ({c['cite']}) {c['text']}" for i, (c, _) in enumerate(hits))
    prompt = (
        "You answer questions about a recorded session using ONLY the context excerpts "
        "below. Cite the bracketed source tags you used, e.g. [2]. If the answer is not "
        "in the context, say you can't find it in the recording.\n\n"
        f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:")
    answer = chat(prompt, llm)
    return answer, hits


# -------------------------------- CLI ---------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="Local RAG Q&A over a session")
    p.add_argument("cmd", choices=["index", "ask", "chat"])
    p.add_argument("source", help="session dir, .tex report, transcript .json, or text file")
    p.add_argument("question", nargs="?", help="question (for 'ask')")
    p.add_argument("--llm", default=LLM_MODEL)
    p.add_argument("--embed-model", default=EMBED_MODEL)
    p.add_argument("--k", type=int, default=TOP_K)
    a = p.parse_args()

    if a.cmd == "index":
        build_index(a.source, a.embed_model)
        return
    if a.cmd == "ask":
        if not a.question:
            sys.exit("provide a question")
        ans, hits = ask(a.source, a.question, a.llm, a.k)
        print("\n" + ans + "\n")
        print("— sources —")
        for i, (c, s) in enumerate(hits, 1):
            print(f"  [{i}] {c['cite']}  (score {s:.2f})")
        return
    if a.cmd == "chat":
        index = load_index(a.source) or (build_index(a.source, a.embed_model), load_index(a.source))[1]
        print("Ask about the session (Ctrl-C or blank line to quit).")
        while True:
            try:
                q = input("\n❓ ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); break
            if not q:
                break
            ans, hits = ask(a.source, q, a.llm, a.k, index=index)
            print("\n" + ans)
            print("  sources: " + ", ".join(f"[{i+1}] {c['cite']}" for i, (c, _) in enumerate(hits)))


if __name__ == "__main__":
    main()
