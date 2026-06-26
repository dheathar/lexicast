"""PDF / LaTeX / Markdown -> narrated audiobook + karaoke HTML.

Pipeline:
  .pdf            -> extract_text -> classify (Jenks font sizes) ->
  .tex/.md        -> extract_markdown (clean text, structural labels) ->
                  -> classified_text.json
                  -> tts (Kokoro)  -> temp/block_*.wav + timeline.json
                  -> join_audios   -> audiobook.mp3
                  -> make_karaoke  -> karaoke.html

Examples:
  python main.py book.pdf
  python main.py paper.tex --voice am_michael
  python main.py notes.md --out mybook.mp3 --embed
  python main.py book.pdf --steps audio,join,karaoke   # reuse classified_text.json
"""

import argparse
import json
import os
import sys

ALL_STEPS = ["extract", "audio", "join", "karaoke"]


def run(args):
    classified = "classified_text.json"
    steps = args.steps.split(",") if args.steps else ALL_STEPS

    # 1. Extract -> classified_text.json
    if "extract" in steps:
        ext = os.path.splitext(args.input)[1].lower()
        if ext == ".pdf":
            import extract_text, classify
            print(f"📖 Extracting PDF: {args.input}")
            data = extract_text.extract_from_pdf(args.input)
            print(f"   {len(data)} blocks; classifying by font size...")
            data = classify.classify_font_sizes(data)
        elif ext in (".tex", ".latex", ".md", ".markdown"):
            import extract_markdown
            print(f"📖 Extracting clean text: {args.input}")
            data = extract_markdown.extract(args.input)
        else:
            sys.exit(f"❌ Unsupported input type: {ext} (use .pdf, .tex or .md)")
        with open(classified, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"   -> {classified} ({len(data)} blocks)")

    # 1b. Optional LLM text normalization (numbers/abbreviations/OCR cleanup)
    if args.normalize and ("extract" in steps or "normalize" in steps):
        if not os.path.exists(classified):
            sys.exit(f"❌ {classified} missing — run the extract step first.")
        import normalize
        print(f"📝 Normalizing text via Ollama ({args.normalize_model})...")
        with open(classified, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = normalize.normalize_blocks(data, model=args.normalize_model)
        with open(classified, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 2. Synthesize -> temp/block_*.wav + timeline.json
    if "audio" in steps:
        if not os.path.exists(classified):
            sys.exit(f"❌ {classified} missing — run the extract step first.")
        import tts
        print(f"🔊 Synthesizing with Kokoro (voice={args.voice}, lang={args.lang})...")
        tts.generate_audiobook(input_json=classified, voice=args.voice,
                               lang=args.lang, speed=args.speed)

    # 3. Join -> audiobook.mp3
    if "join" in steps:
        import join_audios
        print("🎚️  Joining blocks...")
        join_audios.join(output=args.out)

    # 4. Karaoke -> karaoke.html
    if "karaoke" in steps and not args.no_karaoke:
        import make_karaoke
        title = os.path.splitext(os.path.basename(args.input))[0]
        make_karaoke.build(audio_file=args.out, output_html=args.karaoke,
                           title=title, embed=args.embed)
        make_karaoke.write_cues()  # standard WebVTT + LRC alongside

    print("\n🎧 Done.")
    print(f"   audio:    {args.out}")
    if not args.no_karaoke:
        print(f"   karaoke:  {args.karaoke}  (open in a browser)")


def main():
    p = argparse.ArgumentParser(description="PDF/LaTeX/Markdown -> audiobook + karaoke HTML")
    p.add_argument("input", help="input file: .pdf, .tex or .md")
    p.add_argument("--voice", default="af_heart",
                   help="Kokoro voice (af_heart, af_bella, am_michael, bf_emma, ...)")
    p.add_argument("--lang", default="a",
                   help="Kokoro language code (a=US, b=UK, e=ES, f=FR, i=IT, p=PT, h=HI, j=JA, z=ZH)")
    p.add_argument("--speed", type=float, default=1.0, help="speech speed multiplier")
    p.add_argument("--out", default="audiobook.mp3", help="output audio file (.mp3/.m4a)")
    p.add_argument("--karaoke", default="karaoke.html", help="output karaoke HTML")
    p.add_argument("--no-karaoke", action="store_true", help="skip the karaoke page")
    p.add_argument("--embed", action="store_true",
                   help="inline the audio into the HTML (one portable file)")
    p.add_argument("--normalize", action="store_true",
                   help="rewrite text for speech via a local Ollama LLM before TTS")
    p.add_argument("--normalize-model", default="qwen3:8b",
                   help="Ollama model for --normalize (e.g. qwen3:8b, gemma4:latest)")
    p.add_argument("--steps", default="",
                   help="comma list of steps to run: extract,audio,join,karaoke")
    run(p.parse_args())


if __name__ == "__main__":
    main()
