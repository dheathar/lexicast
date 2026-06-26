"""Clean-text front end for LaTeX (.tex) and Markdown (.md) inputs.

LaTeX is the messy case, so we delegate to pandoc (robust with math, citations,
environments, macros) to convert .tex -> GitHub-flavoured Markdown. Markdown is
then parsed structurally into the same block format the PDF path produces:

    [{"text": ..., "label": "header"|"body"|"other", "section": "..."}]

Because the text is already clean, this path skips extract_text.py + classify.py
entirely. Headings become "header" blocks (longer pauses); paragraphs/list items
become "body"; code blocks, tables and images are labelled "other" (skipped by
the TTS step) since they don't narrate well.
"""

import json
import re
import shutil
import subprocess
import sys

OUTPUT_JSON = "classified_text.json"


def tex_to_markdown(tex_path):
    """Convert a LaTeX file to Markdown using pandoc."""
    if not shutil.which("pandoc"):
        sys.exit("❌ pandoc not found. Install it (e.g. `brew install pandoc`).")
    # gfm = GitHub-flavoured Markdown; --wrap=none keeps paragraphs on one line.
    result = subprocess.run(
        ["pandoc", tex_path, "-f", "latex", "-t", "gfm", "--wrap=none"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"❌ pandoc failed:\n{result.stderr}")
    return result.stdout


# --- inline markdown cleanup --------------------------------------------------

_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")                 # images
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")               # [text](url) -> text
_REFLINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")           # [text][ref] -> text
_INLINE_CODE = re.compile(r"`([^`]*)`")                    # `code` -> code
_EMPH = re.compile(r"(\*\*|\*|__|_|~~)")                   # bold/italic/strike marks
_HTML = re.compile(r"<[^>]+>")                             # stray html tags
_FOOTNOTE = re.compile(r"\[\^[^\]]*\]")                    # footnote refs
_MATH = re.compile(r"\$\$?(.+?)\$\$?", re.DOTALL)          # $..$ / $$..$$ inline math
_MULTISPACE = re.compile(r"[ \t]+")


def _despeak_math(m):
    """Make leftover inline math roughly speakable: drop $, ^, _, braces, latex cmds."""
    s = m.group(1)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)     # \alpha, \frac, ...
    s = s.replace("^", " ").replace("_", " ")
    s = re.sub(r"[{}\\]", "", s)
    return " " + _MULTISPACE.sub(" ", s).strip() + " "


def clean_inline(text):
    text = _IMG.sub("", text)
    text = _FOOTNOTE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _REFLINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _MATH.sub(_despeak_math, text)
    text = _EMPH.sub("", text)
    text = _HTML.sub("", text)
    text = text.replace("\\", "")          # leftover latex escapes
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def parse_markdown(md_text):
    """Parse Markdown into labelled narration blocks."""
    blocks = []
    current_section = ""
    in_code = False
    para_lines = []

    def flush_paragraph():
        nonlocal para_lines
        if not para_lines:
            return
        text = clean_inline(" ".join(para_lines))
        if text:
            blocks.append({"text": text, "label": "body", "section": current_section})
        para_lines = []

    lines = md_text.splitlines()
    # Drop a YAML front-matter block if present.
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1:]

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        # Fenced code blocks -> skipped narration.
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            in_code = not in_code
            continue
        if in_code:
            continue

        if not stripped:
            flush_paragraph()
            continue

        # Headings (# .. ######)
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_paragraph()
            heading = clean_inline(m.group(2)).rstrip("#").strip()
            if heading:
                current_section = heading
                blocks.append({"text": heading, "label": "header", "section": heading})
            continue

        # Horizontal rules -> ignore
        if re.match(r"^([-*_])\1{2,}$", stripped.replace(" ", "")):
            flush_paragraph()
            continue

        # Markdown tables -> skipped (don't narrate well)
        if stripped.startswith("|") or re.match(r"^[:\-\| ]+$", stripped):
            flush_paragraph()
            blocks.append({"text": "", "label": "other", "section": current_section})
            continue

        # Blockquotes / list items: strip leading markers, treat as body text.
        stripped = re.sub(r"^>\s?", "", stripped)
        stripped = re.sub(r"^(\s*)([-*+]|\d+\.)\s+", "", stripped)
        para_lines.append(stripped)

    flush_paragraph()
    # Drop empty placeholder blocks.
    return [b for b in blocks if b["text"] or b["label"] == "other"]


def extract(path):
    """Path -> list of labelled blocks. Accepts .tex or .md."""
    lower = path.lower()
    if lower.endswith((".tex", ".latex")):
        md = tex_to_markdown(path)
    else:  # .md / .markdown / anything else: treat as markdown/plain text
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()
    return parse_markdown(md)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python extract_markdown.py <file.tex|file.md>")
    data = extract(sys.argv[1])
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Extracted {len(data)} blocks -> {OUTPUT_JSON}")
