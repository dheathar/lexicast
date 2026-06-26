"""Font-size classification via Jenks natural breaks.

Groups PDF blocks into header / body / caption / other based on their average
font size, so the TTS step can add appropriate pauses and skip noise.

Only used for the PDF path. Clean inputs (.tex/.md) are labelled structurally
in extract_markdown.py instead.
"""

import json
import jenkspy

INPUT_JSON = "vision_output.json"
OUTPUT_JSON = "classified_text.json"


def classify_font_sizes(data, n_classes=4):
    font_sizes = [b["avg_font_size"] for b in data if b.get("avg_font_size", 0) > 0]

    # Jenks needs more distinct values than classes; fall back gracefully.
    n_classes = min(n_classes, max(1, len(set(font_sizes))))
    if n_classes < 2:
        for block in data:
            block["label"] = "body"
        return data

    breaks = jenkspy.jenks_breaks(font_sizes, n_classes)
    print("Jenks breaks:", breaks)

    def get_label(size):
        if size < breaks[1]:
            return "other"     # small text, e.g. footnotes
        elif size < breaks[2]:
            return "caption"   # captions
        elif size < breaks[3]:
            return "body"      # main text
        else:
            return "header"    # titles / headers

    for block in data:
        block["label"] = get_label(block["avg_font_size"])

    return data


if __name__ == "__main__":
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    classified = classify_font_sizes(data)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)
    print(f"✅ Classified text saved to {OUTPUT_JSON}")
