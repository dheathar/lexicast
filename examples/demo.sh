#!/usr/bin/env bash
# Runnable demo: turn the bundled sample documents into an audiobook + synced
# transcript page. Uses the project venv if present.
#
#   ./examples/demo.sh           # uses sample.md
#   ./examples/demo.sh tex       # uses sample.tex (needs pandoc)
#
# First run downloads the Kokoro model (~few hundred MB).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

SRC="sample.md"
[ "${1:-}" = "tex" ] && SRC="sample.tex"

echo "▶ Building audiobook + synced transcript from $SRC ..."
"$PY" main.py "$SRC" --out demo_audiobook.mp3 --synced demo_synced.html --title "lexicast demo"

echo
echo "✅ Done:"
echo "   audio : demo_audiobook.mp3"
echo "   synced: demo_synced.html   (open in a browser)"
echo
case "$(uname)" in
  Darwin) echo "   tip: open demo_synced.html" ;;
  *)      echo "   tip: xdg-open demo_synced.html" ;;
esac
