"""Seed the personal lexicon (lexicon.db) from structured sources.

Both importers are idempotent: re-running against a grown corpus raises
frequencies (MAX, not +=) and adds only genuinely new surfaces.

  --tbwiki PATH   tb_wiki research hub (read-only mount). Mines *.md prose
                  under research/ and vault/ (domain = first two path
                  components), plus curated filename terms regexes can't
                  reach: vault/People/* and vault/Clusters/By-Client/* stems
                  and research/ top-level project folder names.
  --banks A,B,C   Hindsight banks via REST: memory text + the pre-extracted
                  entity string ("Alice (PERSON), Google (ORGANIZATION)").
                  Entities bypass the noise gate and may be Greek. panos-home
                  is deliberately NOT in the default set (family names are
                  PII) -- pass it explicitly to opt in.

The /v1 API enforces keys (verified 2026-09-09). Key comes from
HINDSIGHT_API_KEY env or lexicon/hindsight.env (git-ignored); never commit it.

Run inside the container (espeak-ng + requests live there):
  docker exec lexicast-lexicast-1 python lexicon_import.py --tbwiki /tb_wiki
  docker exec lexicast-lexicast-1 python lexicon_import.py --banks panos-projects,panos-world-bank
"""

import argparse
import collections
import os
import re
import sys

import requests

import lexicon

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(APP_DIR, "lexicon", "hindsight.env")
# Deliberate default: work banks only. panos-home holds family names (PII) --
# it is never mined unless passed explicitly.
DEFAULT_BANKS = ["panos-projects", "panos-world-bank", "panos-dmlab",
                 "panos-ece-papel", "panos-notes", "harness_dev",
                 "panos-homelab"]

SKIP_DIRS = {".git", ".obsidian", ".stfolder", ".stversions", "node_modules",
             ".venv", "__pycache__", ".pytest_cache", "copilot", "Dashboards"}
CURATED_DIR_SKIP = {"wiki", "skills", "Papers"}

ENT_RE = re.compile(r"^(.*?)\s*\([A-Za-z_]+\)$")


def _load_key():
    key = os.environ.get("HINDSIGHT_API_KEY")
    if key:
        return key.strip()
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if line.startswith("HINDSIGHT_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


def _title_case(stem):
    return " ".join(w.capitalize() for w in re.split(r"[-_ ]+", stem) if w)


class _Agg:
    """Case-insensitive aggregation so upsert sees ONE corpus count per
    surface per run -- the MAX-in-upsert idempotency depends on it."""

    def __init__(self):
        self.by_ci = {}

    def add(self, surface, n, domain):
        ci = surface.lower()
        e = self.by_ci.get(ci)
        if e is None:
            self.by_ci[ci] = {"surface": surface, "n": n, "domain": domain}
        else:
            e["n"] += n

    def flush(self, conn, source):
        created = 0
        for e in self.by_ci.values():
            _, was_new = lexicon.upsert_term(conn, e["surface"],
                                             domain=e["domain"],
                                             source=source, freq=e["n"])
            created += was_new
        conn.commit()
        top = sorted(self.by_ci.values(), key=lambda e: -e["n"])[:10]
        return created, len(self.by_ci) - created, top


def import_tbwiki(conn, root):
    agg = _Agg()
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        parts = [] if rel == "." else rel.split(os.sep)
        domain = "tbwiki/" + "/".join(parts[:2]) if parts else "tbwiki/root"
        for fn in files:
            if not fn.lower().endswith((".md", ".markdown")):
                continue
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > 2_000_000:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            for surface, n in lexicon.extract_candidates(text).items():
                if lexicon.admitted(surface, n):
                    agg.add(surface, n, domain)

    # curated proper nouns prose regexes skip: people/client page stems and
    # research project folder names ("Bruno Silva", "EU-ALMPO", "WorldBank")
    for sub in ("vault/People", "vault/Clusters/By-Client"):
        d = os.path.join(root, *sub.split("/"))
        if os.path.isdir(d):
            for fn in os.listdir(d):
                stem, ext = os.path.splitext(fn)
                if ext.lower() == ".md" and stem.lower() not in ("index",):
                    s = _title_case(stem)
                    if 2 <= len(s) <= 40:
                        agg.add(s, 1, "tbwiki/" + sub)
    rd = os.path.join(root, "research")
    if os.path.isdir(rd):
        for d in sorted(os.listdir(rd)):
            if d in CURATED_DIR_SKIP or d.startswith("."):
                continue
            if os.path.isdir(os.path.join(rd, d)):
                agg.add(d, 1, "tbwiki/research")

    created, merged, top = agg.flush(conn, "tbwiki")
    return {"source": "tbwiki", "created": created, "merged": merged,
            "top": [(e["surface"], e["n"]) for e in top]}


def _entity_surfaces(ent_field):
    """'Alice (PERSON), Google (ORGANIZATION)' -> ['Alice', 'Google']."""
    if not ent_field:
        return []
    out = []
    for part in str(ent_field).split(","):
        part = part.strip()
        if not part:
            continue
        m = ENT_RE.match(part)
        s = (m.group(1) if m else part).strip()
        if 2 <= len(s) <= 60:
            out.append(s)
    return out


def _fetch_all(base, bank, headers):
    items, offset, limit = [], 0, 100
    while True:
        r = requests.get(f"{base}/v1/default/banks/{bank}/memories/list",
                         params={"limit": limit, "offset": offset},
                         headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get("items", [])
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(data.get("total") or 0):
            break
    return items


def import_hindsight(conn, base_url, banks, key):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    summary = []
    for bank in banks:
        memories = _fetch_all(base_url, bank, headers)
        agg = _Agg()
        ents = collections.Counter()
        for mem in memories:
            text = mem.get("text") or ""
            for surface, n in lexicon.extract_candidates(text).items():
                if lexicon.admitted(surface, n):
                    agg.add(surface, n, f"hindsight/{bank}")
            # entities skip the noise gate: they are pre-extracted proper
            # nouns, and they are how Greek names enter the lexicon in v1
            for s in _entity_surfaces(mem.get("entities")):
                ents[s] += 1
                agg.add(s, 1, f"hindsight/{bank}")
        created, merged, _ = agg.flush(conn, f"hindsight/{bank}")
        summary.append({"source": f"hindsight/{bank}", "memories": len(memories),
                        "created": created, "merged": merged,
                        "top_entities": [s for s, _ in ents.most_common(8)]})
    return summary


def main():
    ap = argparse.ArgumentParser(
        description="seed lexicon.db from structured sources")
    ap.add_argument("--tbwiki", metavar="PATH",
                    help="tb_wiki hub root (container mount: /tb_wiki)")
    ap.add_argument("--banks", default=None,
                    help="comma-separated Hindsight bank ids (default set is "
                         "work banks only; NO panos-home -- pass it "
                         "explicitly to opt in)")
    ap.add_argument("--base-url",
                    default=os.environ.get("HINDSIGHT_URL",
                                           "http://100.75.182.91:8888"))
    a = ap.parse_args()
    if not a.tbwiki and not a.banks:
        ap.error("nothing to do: pass --tbwiki and/or --banks")
    conn = lexicon.connect()
    lexicon.init_db(conn)
    results = []
    if a.tbwiki:
        if not os.path.isdir(a.tbwiki):
            sys.exit(f"--tbwiki path not found: {a.tbwiki}")
        results.append(import_tbwiki(conn, a.tbwiki))
    if a.banks:
        key = _load_key()
        if not key:
            sys.exit("no Hindsight API key: set HINDSIGHT_API_KEY or create "
                     "lexicon/hindsight.env")
        banks = [b.strip() for b in a.banks.split(",") if b.strip()]
        results.extend(import_hindsight(conn, a.base_url, banks, key))
    for r in results:
        line = f"{r['source']}: {r['created']} new / {r['merged']} merged terms"
        if "memories" in r:
            line += f" (from {r['memories']} memories)"
        if r.get("top"):
            line += "; top: " + ", ".join(f"{s}({n})" for s, n in r["top"][:6])
        if r.get("top_entities"):
            line += "; entities: " + ", ".join(r["top_entities"])
        print(line)
    lexicon.stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
