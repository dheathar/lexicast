"""Enrich a handout with references, SOTA, an alignment section, and per-topic
links — rendered generically from PER-SESSION DATA, so the tool stays domain-agnostic.

Nothing topic-specific is hardcoded here. Inputs (all optional, in <session_dir>):
  references.json        {regulation[], tools[], sota[], verify[], topic_links[]}
                         each ref: {title, url, note}; verify: plain strings;
                         topic_links: {keywords:[...], links:[{text,url}]}
  alignment_section.tex  raw LaTeX for an "applying this to <app>" section

With no references.json/alignment_section.tex this just rebuilds the handout
unchanged — future-proof. Generate references.json per session (the model can
propose named tools/regulations + search queries; resolve/verify URLs via web
search before committing).

  .venv/bin/python enrich_handout.py <session_dir> [--title "..."]
"""

import json
import os

import make_latex
import make_handout

esc = make_latex.esc


def link(url, text):
    return rf"\href{{{url}}}{{{esc(text)}}}"


def _ref_section(title, refs, lead=""):
    if not refs:
        return ""
    items = "\n".join(
        rf"  \item {link(r['url'], r['title'])}" + (rf" --- {esc(r['note'])}" if r.get("note") else "")
        for r in refs)
    head = f"\\section*{{{title}}}\n"
    if lead:
        head += rf"\textit{{\small {esc(lead)}}}\par\vspace{{0.3em}}" + "\n"
    return head + f"\\begin{{itemize}}[leftmargin=1.4em]\n{items}\n\\end{{itemize}}"


def _verify_section(items):
    if not items:
        return ""
    body = "\n".join(rf"  \item {esc(v)}" for v in items)
    return ("\\section*{Worth Verifying \\& Exploring Further}\n"
            "\\textit{\\small Items mentioned that need a primary source or a closer look.}"
            "\\par\\vspace{0.3em}\n"
            f"\\begin{{itemize}}[leftmargin=1.4em]\n{body}\n\\end{{itemize}}")


def attach_topic_links(handout, topic_links):
    for t in handout.get("topics", []):
        hay = (t.get("title", "") + " " + t.get("overview", "")).lower()
        found = []
        for rule in topic_links:
            if any(k.lower() in hay for k in rule.get("keywords", [])):
                found += [link(l["url"], l["text"]) for l in rule.get("links", [])]
        seen, uniq = set(), []
        for l in found:
            if l not in seen:
                seen.add(l); uniq.append(l)
        if uniq:
            t["_links"] = " · ".join(uniq[:2])
    return handout


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("session_dir")
    p.add_argument("--title", default="")
    p.add_argument("--date", default="2026-06-26")
    a = p.parse_args()

    d = a.session_dir.rstrip("/")
    handout = json.load(open(os.path.join(d, "handout.json"), encoding="utf-8"))
    title = a.title or os.path.basename(d).replace("_session", "")

    refs_path = os.path.join(d, "references.json")
    refs = json.load(open(refs_path, encoding="utf-8")) if os.path.exists(refs_path) else {}

    attach_topic_links(handout, refs.get("topic_links", []))
    resources = (_ref_section("Tools, Frameworks \\& Regulation", refs.get("regulation", []))
                 + "\n\n"
                 + _ref_section("More tools \\& documentation standards", refs.get("tools", [])))
    sota = _ref_section("State of the Art \\& Key Research", refs.get("sota", []))
    verify = _verify_section(refs.get("verify", []))

    align_path = os.path.join(d, "alignment_section.tex")
    alignment = open(align_path, encoding="utf-8").read() if os.path.exists(align_path) else ""

    out_tex = os.path.join(d, "handout_v2.tex")
    make_handout.render(handout, {"Title": title, "Date": a.date}, out_tex,
                        resources=resources, sota=sota, verify=verify, alignment=alignment)
    import compile_pdf
    compile_pdf.compile_pdf(out_tex)
    print(f"✅ {os.path.join(d, 'handout_v2.pdf')}")


if __name__ == "__main__":
    main()
