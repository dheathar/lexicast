"""Personal lexicon store -- the Layer-3 "Personal Context Store" of the
personalized STT design (speech-personal/arch.txt), built into lexicast.

One SQLite database (default lexicon/lexicon.db; volume-mounted, git-ignored)
holding the terms generic ASR reliably gets wrong for this speaker: project
acronyms (MySMIS, MIPE, ICATUS), collaborator and client names, machine
names. Every term carries an espeak-ng IPA pronunciation, because retrieval
must match PHONETICALLY -- when Whisper hears "my smiss", the embedding of
the error is semantically nowhere near MySMIS, but the phoneme sequence is.

Population happens in lexicon_import.py (seed sources: tb_wiki hub, Hindsight
banks; more extractors plug in) and later via transcript corrections from the
/transcribe feedback endpoint. This module owns the store and the retrieval
primitive the future two-pass decode (faster-whisper `hotwords` re-decode)
calls between passes.

espeak-ng does G2P for both English (en-us) and Greek (el); it is already in
this image for Kokoro/misaki, and the SAME binary phonemizes at import time
and at decode time, so IPA forms stay comparable by construction. Do not
switch one side's phonemizer without the other.

CLI (inside the container):
  python lexicon.py init
  python lexicon.py stats
  python lexicon.py dump-review [--out PATH]
  python lexicon.py retrieve "my smiss funding" [--domain X] [--k 10]
  python lexicon.py confirm SURFACE [SURFACE...]
  python lexicon.py drop SURFACE [SURFACE...]
"""

import argparse
import collections
import datetime
import json
import os
import re
import sqlite3
import subprocess
import unicodedata

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("LEXICON_DB", os.path.join(APP_DIR, "lexicon", "lexicon.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS terms(
  id INTEGER PRIMARY KEY,
  surface TEXT NOT NULL,            -- canonical casing, e.g. "MySMIS"
  surface_ci TEXT NOT NULL UNIQUE,  -- lowercase fold; the merge key
  ipa TEXT,                         -- espeak-ng --ipa output
  voice TEXT,                       -- espeak-ng voice used ('en-us' | 'el')
  frequency INTEGER DEFAULT 0,      -- corpus count as of last import (MAX)
  hits INTEGER DEFAULT 0,           -- retrieval hits (pipeline bumps this)
  domain TEXT,                      -- topic scope ('tbwiki/research/WorldBank', 'hindsight/panos-projects')
  source TEXT,                      -- importer id, provenance for audit
  status TEXT DEFAULT 'candidate',  -- candidate | confirmed
  first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS aliases(
  id INTEGER PRIMARY KEY,
  term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
  surface TEXT NOT NULL,            -- alt spelling OR observed ASR-error form
  kind TEXT,                        -- 'import' | 'correction' | 'asr_error'
  ipa TEXT,
  UNIQUE(term_id, surface));
CREATE TABLE IF NOT EXISTS hits(
  id INTEGER PRIMARY KEY,
  term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
  distance REAL, context TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS idx_terms_voice ON terms(voice);
CREATE INDEX IF NOT EXISTS idx_terms_domain ON terms(domain);
CREATE INDEX IF NOT EXISTS idx_aliases_term ON aliases(term_id);
"""


def connect(db_path=None):
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # worker writes while Flask polls
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    version = "unknown"
    try:
        fp = subprocess.run(["espeak-ng", "--version"], capture_output=True,
                            text=True, timeout=10)
        raw = (fp.stdout or fp.stderr).strip()
        version = raw.splitlines()[0] if raw else "unknown"
    except Exception:
        pass
    # G2P fingerprint: a different espeak-ng can emit different IPA, which
    # would silently break every stored pronunciation. Recorded so a future
    # import can detect the drift and re-phonemize instead of guessing.
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('espeak_ng', ?)",
                 (version,))
    conn.commit()


# --- G2P ---------------------------------------------------------------------

GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")


def script_of(text):
    return "el" if GREEK_RE.search(text) else "en-us"


def phonemize(text, voice=None):
    """IPA for `text` via espeak-ng. Text goes in through stdin so nothing
    can be misread as a flag. Returns None on any failure -- a term without
    IPA simply never matches, rather than crashing an import."""
    voice = voice or script_of(text)
    try:
        out = subprocess.run(["espeak-ng", "--ipa", "-q", "-v", voice],
                             input=text, capture_output=True, text=True,
                             timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return " ".join(out.stdout.split()) or None


# --- writes ------------------------------------------------------------------

def upsert_term(conn, surface, domain=None, source=None, freq=1,
                status="candidate"):
    """Merge by case-insensitive surface. frequency=MAX(existing, freq):
    importers aggregate per-run counts, so a re-import of a grown corpus
    raises the number while a re-import of the same corpus is idempotent
    (a plain += would double-count every refresh). First-seen domain wins;
    later sources only fill NULLs. Returns (term_id, created)."""
    surface = surface.strip()
    if not surface:
        return None, False
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    ci = surface.lower()
    row = conn.execute("SELECT id FROM terms WHERE surface_ci=?", (ci,)).fetchone()
    if row:
        conn.execute(
            "UPDATE terms SET frequency=MAX(frequency, ?), last_seen=?, "
            "domain=COALESCE(domain, ?), source=COALESCE(source, ?), "
            "status=CASE WHEN ?='confirmed' THEN 'confirmed' ELSE status END "
            "WHERE id=?",
            (freq, now, domain, source, status, row["id"]))
        return row["id"], False
    voice = script_of(surface)
    cur = conn.execute(
        "INSERT INTO terms(surface, surface_ci, ipa, voice, frequency, domain,"
        " source, status, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (surface, ci, phonemize(surface, voice), voice, freq, domain, source,
         status, now, now))
    return cur.lastrowid, True


def add_alias(conn, term_id, alias, kind="import"):
    alias = (alias or "").strip()
    if not alias or not term_id:
        return
    conn.execute("INSERT OR IGNORE INTO aliases(term_id, surface, kind, ipa)"
                 " VALUES(?,?,?,?)",
                 (term_id, alias, kind, phonemize(alias)))


# --- regex mining ------------------------------------------------------------

# Only obvious noise: shouty markdown emphasis and acronyms Whisper already
# knows cold. Kept SMALL on purpose -- frequency ranking handles the rest.
STOP = {
    "THE", "AND", "FOR", "NOT", "ALL", "ANY", "BUT", "YOU", "CAN", "WILL",
    "THIS", "THAT", "WITH", "FROM", "INTO", "TODO", "TBD", "OK", "YES", "NO",
    "NOTE", "NOTES", "SEE", "USE", "PDF", "HTML", "HTTP", "HTTPS", "XML",
    "JSON", "API", "URL", "CPU", "GPU", "RAM", "SSD", "USB", "FAQ", "CEO",
    "CFO", "ETA", "ASAP", "PS", "IE", "EG", "VS", "ETC", "AI", "ML", "OS",
    "PC", "IT", "HR", "EU", "US", "UK", "UN", "USD", "EUR", "GBP", "VAT",
    "LLM", "GPT", "SDK", "IDE", "CLI", "GUI", "CMS", "CRM", "ERP", "SQL",
    "CSS", "PHP", "TCP", "IP", "VPN", "DNS", "SSH", "SSO", "CSV", "IBM",
}
STOP_LC = {s.lower() for s in STOP}

RE_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]+)*\b")
# One internal lower->upper transition is the whole CamelCase signature; the
# form must anchor only at \b and allow a capitalized start, else PascalCase
# with an acronym tail is invisible: MySMIS, GitHub, OpenAI, MySQL, Voxtype-adjacent.
RE_CAMEL = re.compile(r"\b[A-Za-z][a-z]*[A-Z][A-Za-z0-9]*\b")
RE_MULTI = re.compile(r"\b[A-Z][a-z]{2,}(?:[ -][A-Z][a-z]{2,})+\b")
RE_MD = re.compile(r"`+[^`]*`+|\*\*|\[([^\]]*)\]\([^)]*\)")


def extract_candidates(text):
    """Regex mining -> Counter {surface: n}. Deliberately over-inclusive:
    everything lands as status='candidate'; the review pass and frequency
    ranking prune. Markdown links keep their text; code spans and bold
    markers are stripped (a fenced code block collapses whole, so code
    identifiers are NOT mined from prose -- a future tree-sitter importer
    owns those)."""
    def _md(m):
        return m.group(1) if m.group(1) is not None else ""
    text = RE_MD.sub(_md, text)
    counts = collections.Counter()
    for t in RE_ACRONYM.findall(text):
        if t not in STOP:
            counts[t] += 1
    for t in RE_CAMEL.findall(text):
        if t.lower() not in STOP_LC:
            counts[t] += 1
    counts.update(RE_MULTI.findall(text))
    return counts


def admitted(surface, count):
    """Import-time noise gate for regex-mined candidates. Hindsight entities
    bypass this (they arrive pre-extracted); Greek enters only that way in
    v1 -- mining Greek prose would flood the lexicon with common words."""
    if GREEK_RE.search(surface):
        return False
    if not (2 <= len(surface) <= 40):
        return False
    if surface.upper() == surface:          # ALLCAPS: acronym or shouty md
        return len(surface) >= 4 or count >= 3
    if " " in surface or "-" in surface:    # "European Commission" style
        return count >= 2
    return True                              # CamelCase: rare by construction


# --- retrieval ---------------------------------------------------------------

IPA_NOISE = set("ˈˌːˑ‿(),.")


def _clean_ipa(ipa):
    """Strip stress marks/length/ties/punctuation so distance compares bare
    phonemes (MySMIS -> /maɪˈɛsmɪs/ vs heard "my smiss" -> /maɪ smɪs/ must
    compare clean)."""
    if not ipa:
        return ""
    return "".join(ch for ch in ipa
                   if ch not in IPA_NOISE and not ch.isspace()
                   and not unicodedata.combining(ch))


def _levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def retrieve(conn, query, max_dist=0.45, k=10, domain=None, record=False):
    """Phonetic lookup: slide 1-5-word windows over `query`, phonemize each
    with espeak-ng, return lexicon terms whose IPA is within `max_dist`
    normalized edit distance. This is the primitive the two-pass decode
    calls on the first-pass hypothesis -- winners feed faster-whisper
    `hotwords` and a re-decode. `record=True` logs hit rows and bumps hit
    counts: the frequency/decay signal. Full scan per window is fine at
    personal-lexicon scale (thousands of terms); a phoneme-trigram index
    earns its way in only if this ever gets slow."""
    words = [w for w in re.split(r"\s+", (query or "").strip()) if w]
    if not words:
        return []
    sql = "SELECT id, surface, ipa, voice, frequency, domain, status FROM terms"
    args = ()
    if domain:
        sql += " WHERE domain=?"
        args = (domain,)
    rows = list(conn.execute(sql, args))
    best = {}
    for n in (5, 4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            win = " ".join(words[i:i + n])
            wvoice = script_of(win)
            qipa = _clean_ipa(phonemize(win, wvoice))
            if not qipa:
                continue
            for row in rows:
                if row["voice"] != wvoice:
                    continue
                tipa = _clean_ipa(row["ipa"])
                if not tipa:
                    continue
                d = _levenshtein(qipa, tipa) / max(len(qipa), len(tipa), 1)
                if d <= max_dist:
                    score = (1.0 - d) * (1.0 + min(row["frequency"], 50) / 25.0)
                    if row["id"] not in best or score > best[row["id"]][0]:
                        best[row["id"]] = (score, d, row, win)
    ranked = sorted(best.values(), key=lambda t: -t[0])[:k]
    if record:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        for _, d, row, win in ranked:
            conn.execute("INSERT INTO hits(term_id, distance, context, ts)"
                         " VALUES(?,?,?,?)", (row["id"], d, query[:200], now))
            conn.execute("UPDATE terms SET hits=hits+1 WHERE id=?", (row["id"],))
        conn.commit()
    return [{"term": r["surface"], "distance": round(d, 3), "window": w,
             "domain": r["domain"], "frequency": r["frequency"],
             "status": r["status"]} for _, d, r, w in ranked]


# --- reporting / CLI ---------------------------------------------------------

def dump_review(conn, out_path=None):
    """Human pruning pass: every term grouped by domain, most frequent first.
    Informational only -- nothing reads it back; pruning happens via
    `lexicon.py drop`, promotion via `lexicon.py confirm`."""
    out_path = out_path or os.path.join(os.path.dirname(DB_PATH), "review.md")
    rows = list(conn.execute(
        "SELECT domain, surface, frequency, ipa, source, status"
        " FROM terms ORDER BY domain, frequency DESC, surface"))
    by_domain = collections.OrderedDict()
    for r in rows:
        by_domain.setdefault(r["domain"] or "(none)", []).append(r)
    lines = ["# Lexicon review", "",
             f"{len(rows)} terms as of {datetime.date.today().isoformat()}. "
             "Prune: python lexicon.py drop <surface> ; "
             "promote: python lexicon.py confirm <surface>", ""]
    for dom, rs in by_domain.items():
        lines += [f"## {dom} ({len(rs)})", "",
                  "| surface | freq | ipa | source | status |",
                  "|---|---|---|---|---|"]
        lines += [f"| {r['surface']} | {r['frequency']} | {r['ipa'] or ''} "
                  f"| {r['source'] or ''} | {r['status']} |" for r in rs]
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def stats(conn):
    total = conn.execute("SELECT COUNT(*) c FROM terms").fetchone()["c"]
    print(f"terms: {total}")
    for r in conn.execute("SELECT status, COUNT(*) c FROM terms GROUP BY status"):
        print(f"  {r['status']}: {r['c']}")
    print("by domain (top 15):")
    for r in conn.execute("SELECT domain, COUNT(*) c FROM terms"
                          " GROUP BY domain ORDER BY c DESC LIMIT 15"):
        print(f"  {r['domain'] or '(none)'}: {r['c']}")


def main():
    p = argparse.ArgumentParser(description="personal lexicon store (Layer 3)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create schema")
    sub.add_parser("stats", help="row counts")
    dr = sub.add_parser("dump-review", help="write review.md")
    dr.add_argument("--out", default=None)
    rt = sub.add_parser("retrieve", help="phonetic lookup")
    rt.add_argument("query")
    rt.add_argument("--domain", default=None)
    rt.add_argument("--k", type=int, default=10)
    cf = sub.add_parser("confirm", help="promote surfaces to confirmed")
    cf.add_argument("surfaces", nargs="+")
    dp = sub.add_parser("drop", help="remove surfaces")
    dp.add_argument("surfaces", nargs="+")
    a = p.parse_args()
    conn = connect()
    if a.cmd == "init":
        init_db(conn)
        print(f"init ok -> {DB_PATH}")
    elif a.cmd == "stats":
        stats(conn)
    elif a.cmd == "dump-review":
        print(dump_review(conn, a.out))
    elif a.cmd == "retrieve":
        print(json.dumps(retrieve(conn, a.query, k=a.k, domain=a.domain),
                         ensure_ascii=False, indent=2))
    elif a.cmd == "confirm":
        for s in a.surfaces:
            cur = conn.execute("UPDATE terms SET status='confirmed'"
                               " WHERE surface_ci=?", (s.lower(),))
            if cur.rowcount == 0:
                print(f"  (not found: {s})")
        conn.commit()
    elif a.cmd == "drop":
        for s in a.surfaces:
            cur = conn.execute("DELETE FROM terms WHERE surface_ci=?",
                               (s.lower(),))
            if cur.rowcount == 0:
                print(f"  (not found: {s})")
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
