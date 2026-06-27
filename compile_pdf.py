"""Compile a .tex to PDF, trying whatever's available.

Order (engine='auto'): tectonic (single binary) -> texlive Docker image
(matches the project's build.sh) -> local pdflatex. Docker/pdflatex run two
passes so the TOC/refs resolve.
"""

import os
import shutil
import subprocess

DOCKER_IMAGE = "texlive/texlive:latest"


def compile_pdf(tex_path, engine="auto"):
    tex_path = os.path.abspath(tex_path)
    d, fname = os.path.dirname(tex_path), os.path.basename(tex_path)
    pdf = os.path.splitext(tex_path)[0] + ".pdf"

    def have(x):
        return shutil.which(x) is not None

    if engine in ("auto", "tectonic") and have("tectonic"):
        subprocess.run(["tectonic", fname], cwd=d, check=True)
    elif engine in ("auto", "docker") and have("docker"):
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{d}:/work", "-w", "/work", DOCKER_IMAGE,
             "sh", "-c",
             f"pdflatex -interaction=nonstopmode {fname} && "
             f"pdflatex -interaction=nonstopmode {fname}"],
            check=True)
    elif engine in ("auto", "pdflatex") and have("pdflatex"):
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=nonstopmode", fname], cwd=d, check=True)
    else:
        raise RuntimeError(
            "No LaTeX engine found. Install tectonic (`brew install tectonic`) "
            "or Docker (uses texlive/texlive), or a TeX distribution with pdflatex.")

    if not os.path.exists(pdf):
        raise RuntimeError(f"compilation finished but {pdf} was not produced")
    return pdf


if __name__ == "__main__":
    import sys
    out = compile_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "auto")
    print(f"✅ {out}")
