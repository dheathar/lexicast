"""Session recording -> speaker-labeled transcript + notes + LaTeX report.

Pipeline:
  media (video/audio) --ffmpeg--> 16kHz wav
                       --faster-whisper--> segments + word timestamps
                       --pyannote (optional)--> speaker labels
    -> transcript.txt / .srt / .vtt / .json
    -> notes (Ollama, structured) -> notes.json / notes.md
    -> session_report.tex   (LaTeX report)
    -> transcript.html      (synced player, reuses the synced engine)

Examples:
  .venv/bin/python session2notes.py meeting.mp4
  .venv/bin/python session2notes.py call.m4a --llm gemma4:12b --max-speakers 3
  .venv/bin/python session2notes.py talk.wav --no-diarize --asr-model large-v3

Diarization needs a Hugging Face token (see diarize.py header) or use --no-diarize.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

import extract_audio
import transcribe as asr
import extract_notes
import make_latex
import make_synced


def _ts_srt(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_transcript_files(segments, out_dir, diarized):
    # plain text — merge consecutive same-speaker fragments into readable turns
    turns = make_latex.merge_turns(segments) if diarized else segments
    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        for s in turns:
            tag = f"{s.get('speaker','')}: " if diarized and s.get("speaker") else ""
            f.write(f"[{make_latex._hms(s['start'])}] {tag}{s['text']}\n")
    # srt
    with open(os.path.join(out_dir, "transcript.srt"), "w", encoding="utf-8") as f:
        for i, s in enumerate(segments, 1):
            tag = f"{s.get('speaker','')}: " if diarized and s.get("speaker") else ""
            f.write(f"{i}\n{_ts_srt(s['start'])} --> {_ts_srt(s['end'])}\n{tag}{s['text']}\n\n")
    # vtt
    with open(os.path.join(out_dir, "transcript.vtt"), "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i, s in enumerate(segments, 1):
            tag = f"{s.get('speaker','')}: " if diarized and s.get("speaker") else ""
            f.write(f"{i}\n{make_synced._ts_vtt(int(s['start']*1000))} --> "
                    f"{make_synced._ts_vtt(int(s['end']*1000))}\n{tag}{s['text']}\n\n")
    # json (raw segments)
    with open(os.path.join(out_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)


def build_synced_html(segments, out_dir, audio_mp3, title, embed=False):
    timeline = [{
        "index": i, "block": i, "label": "body",
        "section": s.get("speaker", "") if s.get("speaker") else "",
        "text": s["text"],
        "start_ms": int(s["start"] * 1000), "end_ms": int(s["end"] * 1000),
    } for i, s in enumerate(s2 for s2 in segments if s2.get("text", "").strip())]
    tl_path = os.path.join(out_dir, "transcript_timeline.json")
    with open(tl_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    make_synced.build(timeline_path=tl_path, audio_file=audio_mp3,
                       output_html=os.path.join(out_dir, "transcript.html"), title=title,
                       embed=embed)


def transcript_for_llm(segments, diarized):
    lines = []
    for s in segments:
        tag = f"{s.get('speaker','')}: " if diarized and s.get("speaker") else ""
        lines.append(f"[{make_latex._hms(s['start'])}] {tag}{s['text']}")
    return "\n".join(lines)


def run(args):
    src = args.input
    if not os.path.exists(src):
        sys.exit(f"❌ not found: {src}")
    title = args.title or os.path.splitext(os.path.basename(src))[0]
    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(src)),
                                            os.path.splitext(os.path.basename(src))[0] + "_session")
    os.makedirs(out_dir, exist_ok=True)
    print(f"📂 output -> {out_dir}")

    # 1. audio
    print("🎬 extracting audio (16kHz mono)...")
    wav = extract_audio.to_wav(src, os.path.join(out_dir, "audio.16k.wav"))
    audio_mp3 = os.path.join(out_dir, "audio.mp3")
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "96k", audio_mp3],
                   check=True, capture_output=True)

    # progress printer (throttled to every ~5%); needs unbuffered stdout to stream
    _ps = {"phase": None, "pct": -10}
    def prog(cur, total, phase):
        pct = int(cur / total * 100) if total else 0
        if phase != _ps["phase"]:
            _ps.update(phase=phase, pct=-10)
        if pct >= _ps["pct"] + 5:
            _ps["pct"] = pct
            print(f"   …{phase} {pct}%", flush=True)

    # 2. transcribe (or reuse a previous transcript.json to skip re-transcribing)
    reuse_path = os.path.join(out_dir, "transcript.json")
    if args.reuse_transcript and os.path.exists(reuse_path):
        with open(reuse_path, encoding="utf-8") as f:
            segments = json.load(f)
        import soundfile as sf
        duration = sf.info(wav).duration
        language = args.language or "en"
        print(f"♻️  reusing {len(segments)} segments from {reuse_path} (skipping transcription)")
    else:
        eng = asr.resolve_engine(args.asr_engine)
        print(f"📝 transcribing ({eng} '{args.asr_model}')...")
        segments, duration, language = asr.transcribe(
            wav, model_size=args.asr_model, language=args.language, engine=eng,
            device=args.device, progress=prog)
        print(f"   {len(segments)} segments, {duration:.0f}s, language={language}")

    # 3. diarize
    diarized = False
    n_speakers = 0
    if not args.no_diarize:
        try:
            import diarize as diar
            print("🎙️  diarizing speakers (pyannote)...")
            turns = diar.diarize(wav, token=args.hf_token, device=args.device,
                                 num_speakers=args.speakers,
                                 min_speakers=args.min_speakers,
                                 max_speakers=args.max_speakers)
            diar.assign_speakers(segments, turns)
            n_speakers = len({s.get("speaker") for s in segments})
            diarized = True
            print(f"   {n_speakers} speakers")
        except Exception as e:
            print(f"⚠️  diarization skipped: {e}\n   (continuing without speaker labels)")

    # 4. transcript files
    write_transcript_files(segments, out_dir, diarized)

    # 5. notes via Ollama
    print(f"🧠 extracting notes (Ollama '{args.llm}')...")
    tl_text = transcript_for_llm(segments, diarized)
    notes = extract_notes.extract_notes(tl_text, model=args.llm, progress=prog)
    with open(os.path.join(out_dir, "notes.json"), "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(extract_notes.notes_to_markdown(notes, title=title))

    # 6. LaTeX report
    meta = {
        "Title": title,
        "Date": datetime.date.today().isoformat(),
        "Duration": make_latex._hms(duration),
        "Language": (language or "?").upper(),
        "Speakers": str(n_speakers) if diarized else "not labelled",
        "ASR model": f"faster-whisper {args.asr_model}",
        "Notes model": args.llm,
    }
    tex_path = os.path.join(out_dir, "session_report.tex")
    make_latex.build(notes, segments, meta, output_tex=tex_path)
    if args.pdf:
        import compile_pdf
        print(f"📄 compiling PDF (engine={args.pdf_engine})...")
        try:
            compile_pdf.compile_pdf(tex_path, engine=args.pdf_engine)
        except Exception as e:
            print(f"⚠️  PDF compile skipped: {e}")

    # 7. synced HTML player
    build_synced_html(segments, out_dir, audio_mp3, title)

    # 8. optional RAG index for Q&A
    if args.rag:
        import rag
        print(f"📚 building RAG index (Ollama '{rag.EMBED_MODEL}')...")
        rag.build_index(out_dir)

    if args.keep_wav is False:
        os.remove(wav)

    print("\n✅ Done. In", out_dir + ":")
    print("   transcript.txt / .srt / .vtt / .json")
    print("   notes.md / notes.json")
    print("   session_report.tex   (pdflatex it for a PDF)")
    print("   transcript.html      (synced player — open in a browser)")


def main():
    p = argparse.ArgumentParser(description="Session recording -> transcript + notes + LaTeX report")
    p.add_argument("input", help="audio/video file (.mp4/.mov/.mp3/.m4a/.wav/...)")
    p.add_argument("--asr-model", default=asr.WHISPER_DEFAULT,
                   help="whisper size: tiny|base|small|medium|large-v3|distil-large-v3")
    p.add_argument("--asr-engine", default="auto",
                   help="auto | mlx (Apple GPU) | faster-whisper (CPU/CUDA)")
    p.add_argument("--language", default=None, help="force language (e.g. en, el); default auto-detect")
    p.add_argument("--llm", default=extract_notes.DEFAULT_MODEL, help="Ollama model for notes")
    p.add_argument("--no-diarize", action="store_true", help="skip speaker labelling")
    p.add_argument("--hf-token", default=None, help="Hugging Face token (else $HF_TOKEN)")
    p.add_argument("--speakers", type=int, default=None, help="exact number of speakers (if known)")
    p.add_argument("--min-speakers", type=int, default=None)
    p.add_argument("--max-speakers", type=int, default=None)
    p.add_argument("--device", default="auto",
                   help="auto | cpu | mps | cuda (auto: MPS on Apple Silicon for diarization)")
    p.add_argument("--title", default="", help="report title (default: filename)")
    p.add_argument("--out-dir", default="", help="output directory")
    p.add_argument("--keep-wav", action="store_true", help="keep the intermediate 16kHz wav")
    p.add_argument("--pdf", action="store_true", help="also compile session_report.tex to PDF")
    p.add_argument("--pdf-engine", default="auto",
                   help="auto | tectonic | docker (texlive) | pdflatex")
    p.add_argument("--rag", action="store_true", help="build a RAG index for Q&A afterwards")
    p.add_argument("--reuse-transcript", action="store_true",
                   help="reuse out_dir/transcript.json instead of re-transcribing "
                        "(e.g. to add diarization without re-running Whisper)")
    run(p.parse_args())


if __name__ == "__main__":
    main()
