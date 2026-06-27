# LaTeX reports, handout & enrichment

Three PDF deliverables come from a session, all self-contained LaTeX (standard
packages only — no exotic classes) compiled via `compile_pdf.py`.

## 1. Session report (meeting minutes)

`make_latex.py` fills `templates/session_report_template.tex`:
metadata header · executive summary · key takeaways · recap · decisions ·
action-items table (action/owner/due) · topics · Q&A (with a separate
**Open / Reflection Prompts** subsection for unanswered questions) · the full
**speaker-labeled, timestamped transcript** (consecutive same-speaker fragments
merged into readable turns).

Produced automatically by `session2notes.py --pdf`, or standalone from `notes.json` + `transcript.json`.

## 2. Participant handout

`make_handout.py <session_dir>` uses the local LLM to turn the notes into a
reader-friendly handout: intro · **Topics Covered & Why They Matter** (each topic
with an overview and a highlighted "Why it matters" callout) · key takeaways · next steps.

```bash
.venv/bin/python make_handout.py meeting_session --title "Workshop" [--llm gemma4:12b]
```
Outputs `handout.{tex,json,pdf}`.

## 3. Enriched handout (v2): links, SOTA, app-alignment

`enrich_handout.py` renders the handout **plus** extra sections from
**per-session data you supply** — the tool itself stays domain-agnostic:

- `<session_dir>/references.json` — curated links rendered as:
  - *Tools, Frameworks & Regulation* and *More tools & documentation standards*
  - *State of the Art & Key Research*
  - *Worth Verifying & Exploring Further*
  - per-topic "learn more" links (keyword-matched)
- `<session_dir>/alignment_section.tex` — raw LaTeX for an "Applying this to <app>" section (e.g. a scorecard of how an application aligns with the session's principles).

```bash
.venv/bin/python enrich_handout.py meeting_session --title "Workshop"   # → handout_v2.pdf
```

With neither file present, this just rebuilds the handout unchanged — future-proof.

### `references.json` shape

```json
{
  "regulation": [{"title": "...", "url": "https://...", "note": "..."}],
  "tools":      [{"title": "...", "url": "https://...", "note": "..."}],
  "sota":       [{"title": "...", "url": "https://...", "note": "..."}],
  "verify":     ["plain-text item to double-check", "..."],
  "topic_links":[{"keywords": ["eu ai act"], "links": [{"text": "EU AI Act", "url": "https://..."}]}]
}
```

> **Authoring model:** the reusable code never hardcodes topic-specific content.
> For each session, author `references.json` (web-verified links) and
> `alignment_section.tex` (e.g. from a code/process audit) — the templates render them.

## Compiling

`compile_pdf.py FILE.tex [engine]` tries, in order (`engine=auto`): **tectonic** →
Docker **`texlive/texlive`** (two passes) → local **pdflatex**. `make_latex.esc()`
maps Unicode punctuation (em/en dashes, curly quotes, ellipsis, bullets, arrows)
to safe LaTeX so model/transcript text never trips font errors.
