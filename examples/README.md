# Examples

## Audiobook + synced transcript (quick demo)

Uses the bundled `sample.md` / `sample.tex`:

```bash
./examples/demo.sh          # from sample.md
./examples/demo.sh tex      # from sample.tex (needs pandoc)
```

Produces `demo_audiobook.mp3` and `demo_synced.html` (open the HTML in a browser).
First run downloads the Kokoro model.

## Session → notes (with your own recording)

```bash
.venv/bin/python session2notes.py path/to/recording.mp4 --pdf --rag
.venv/bin/python rag.py chat recording_session/
```

See [`../docs/sessions.md`](../docs/sessions.md) for options (model, diarization, etc.).
