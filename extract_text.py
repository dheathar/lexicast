"""PDF text extraction (PyMuPDF).

Reads a PDF and produces a list of text blocks with their average font size,
which the classifier later uses to label headers / body / captions / noise.

This is the PDF-specific front end. For .tex / .md inputs the text is already
clean, so that path skips this step entirely (see extract_markdown.py).
"""

import fitz  # PyMuPDF
import json

PDF_PATH = "book.pdf"
OUTPUT_JSON = "vision_output.json"
# Ignore text below this font size
MIN_FONT_SIZE = 2


def extract_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    results = []

    for page_num, page in enumerate(doc, start=1):
        # Skip some pages (if needed)
        # if page_num <= 3:
        #     continue

        # Remove tables so their text isn't read out as gibberish
        for tab in page.find_tables():
            page.add_redact_annot(tab.bbox)
        page.apply_redactions()

        blocks = page.get_text("dict")["blocks"]

        for b in blocks:
            if "lines" not in b:
                continue
            font_sizes = []
            text_content = []

            # Set to True to ignore everything after some unwanted marker
            ignore = False

            for l in b["lines"]:
                if ignore:
                    continue
                for s in l["spans"]:
                    if ignore:
                        continue
                    if s["size"] >= MIN_FONT_SIZE and s["text"].strip():
                        # Ignore lone numbers like page numbers "1", "2", ...
                        try:
                            int(s["text"])
                            continue
                        except ValueError:
                            font_sizes.append(s["size"])
                            text_content.append(s["text"].strip())

            if not text_content:
                continue

            avg_font_size = sum(font_sizes) / len(font_sizes)
            results.append({
                "page": page_num,
                "text": " ".join(text_content),
                "avg_font_size": round(avg_font_size, 2),
            })

    return results


if __name__ == "__main__":
    data = extract_from_pdf(PDF_PATH)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Extracted data saved to {OUTPUT_JSON}")
