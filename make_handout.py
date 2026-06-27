"""Generate a participant-facing handout (topics + why they matter) from notes.

Uses the local Ollama LLM to turn session notes into a reader-friendly summary:
an intro, each topic with a short overview + a "why it matters" note, key
takeaways, and next steps. Renders templates/handout_template.tex and compiles.

  .venv/bin/python make_handout.py <session_dir> [--llm qwen3:8b] [--title "..."] [--pdf]
"""

import json
import os

import make_latex
from extract_notes import _ollama_json, DEFAULT_MODEL

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "templates", "handout_template.tex")

_SYS = (
    "You write a clear, engaging PARTICIPANT HANDOUT summarizing a workshop/session. "
    "From the provided session notes, produce ONLY JSON with keys: "
    "intro (string, 2-4 welcoming sentences setting context), "
    "topics (array of {title, overview, why_it_matters}; overview = 2-3 sentences on "
    "what was covered; why_it_matters = 2-3 sentences on why it matters to participants "
    "and their practice), "
    "takeaways (array of strings), next_steps (array of strings). "
    "Be faithful to the notes; do not invent facts. Write for an educated general reader."
)

_EMPTY = {"intro": "", "topics": [], "takeaways": [], "next_steps": []}


def generate_handout(notes, model=DEFAULT_MODEL):
    src = {k: notes.get(k) for k in
           ("summary", "recap", "topics", "takeaways", "decisions", "action_items")}
    obj = _ollama_json(_SYS, "SESSION NOTES:\n" + json.dumps(src, ensure_ascii=False), model)
    for k, v in _EMPTY.items():
        obj.setdefault(k, v if not isinstance(v, list) else [])
    return obj


def _bullets(items):
    if not items:
        return r"\textit{None.}"
    return ("\\begin{itemize}[leftmargin=1.4em]\n"
            + "\n".join(rf"  \item {make_latex.esc(x)}" for x in items)
            + "\n\\end{itemize}")


def render(handout, meta, out_tex, template=TEMPLATE,
           resources="", sota="", verify="", alignment=""):
    esc = make_latex.esc

    def topic_tex(t):
        why = esc(t.get("why_it_matters", ""))
        links = t.get("_links")  # raw LaTeX (already \href), optional
        if links:
            why += r"\par\vspace{0.2em}{\small\color{grey}Learn more: }" + links
        return (rf"\topicblock{{{esc(t.get('title',''))}}}"
                rf"{{{esc(t.get('overview',''))}}}{{{why}}}")

    topics = "\n\n".join(topic_tex(t) for t in handout.get("topics", []))
    meta_str = "  \\quad|\\quad  ".join(esc(v) for v in meta.values() if v)

    with open(template, encoding="utf-8") as f:
        tmpl = f.read()
    out = (tmpl
           .replace("%%TITLE%%", esc(meta.get("Title", "Session")))
           .replace("%%META%%", meta_str)
           .replace("%%INTRO%%", esc(handout.get("intro", "")))
           .replace("%%TOPICS%%", topics or r"\textit{No topics.}")
           .replace("%%TAKEAWAYS%%", _bullets(handout.get("takeaways", [])))
           .replace("%%NEXTSTEPS%%", _bullets(handout.get("next_steps", [])))
           .replace("%%ALIGNMENT%%", alignment)
           .replace("%%RESOURCES%%", resources)
           .replace("%%SOTA%%", sota)
           .replace("%%VERIFY%%", verify))
    with open(out_tex, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"✅ Handout LaTeX: {out_tex}")
    return out_tex


def main():
    import argparse
    import datetime
    p = argparse.ArgumentParser(description="Participant handout from session notes")
    p.add_argument("session_dir", help="session output dir (with notes.json)")
    p.add_argument("--llm", default=DEFAULT_MODEL)
    p.add_argument("--title", default="")
    p.add_argument("--date", default=datetime.date.today().isoformat())
    p.add_argument("--no-pdf", action="store_true")
    a = p.parse_args()

    notes = json.load(open(os.path.join(a.session_dir, "notes.json"), encoding="utf-8"))
    title = a.title or os.path.basename(a.session_dir.rstrip("/")).replace("_session", "")
    print(f"🧠 generating handout (Ollama '{a.llm}')...")
    handout = generate_handout(notes, model=a.llm)
    out_tex = os.path.join(a.session_dir, "handout.tex")
    render(handout, {"Title": title, "Date": a.date}, out_tex)
    json.dump(handout, open(os.path.join(a.session_dir, "handout.json"), "w"),
              ensure_ascii=False, indent=2)
    if not a.no_pdf:
        import compile_pdf
        print("📄 compiling PDF...")
        compile_pdf.compile_pdf(out_tex)
        print(f"✅ {os.path.join(a.session_dir, 'handout.pdf')}")


if __name__ == "__main__":
    main()
