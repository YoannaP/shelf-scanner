# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic", "pillow", "pillow-heif", "python-dotenv"]
# ///
"""Prototype: read book spines from shelf photos with two Claude models and compare.

Usage: uv run prototype/detect.py PHOTO [PHOTO ...]
Writes prototype/out/<photo>/<model>/{books.json, annotated.jpg, crops/}.
"""

import base64
import io
import json
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageOps
from pillow_heif import register_heif_opener

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "prototype" / "out"
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5"]
MAX_EDGE = 2000
CROP_PAD = 0.15  # fraction of box size added on each side

PROMPT = """This is a photo of a bookshelf. Identify every book whose spine (or cover) is visible.

For each book give:
- title and author as best you can read them. If the author isn't printed, infer it only if you are confident from the title; otherwise leave it empty.
- confidence: "high" if the text is clearly legible, "medium" if partly legible or inferred, "low" if mostly guessed.
- box: the book's spine bounding box as [x_min, y_min, x_max, y_max] in coordinates normalised to 0-1000 relative to the image width and height (0,0 is top-left).

List books in shelf order: top shelf first, left to right within a shelf. Include books you can see but cannot read, with title "" and confidence "low", so nothing is silently dropped."""

SCHEMA = {
    "type": "object",
    "properties": {
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "box": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "author", "confidence", "box"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["books"],
    "additionalProperties": False,
}


def load_photo(path: Path) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    return img


def detect(client: anthropic.Anthropic, model: str, img: Image.Image) -> tuple[list[dict], dict]:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    start = time.time()
    with client.beta.messages.stream(
        model=model,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"{model} refused: {resp.stop_details}")
    text = next(b.text for b in resp.content if b.type == "text")
    stats = {
        "seconds": round(time.time() - start, 1),
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "stop_reason": resp.stop_reason,
    }
    return json.loads(text)["books"], stats


def to_pixels(box: list[int], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return int(x0 * w / 1000), int(y0 * h / 1000), int(x1 * w / 1000), int(y1 * h / 1000)


def save_outputs(img: Image.Image, books: list[dict], stats: dict, out: Path) -> None:
    (out / "crops").mkdir(parents=True, exist_ok=True)
    w, h = img.size
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    colours = {"high": "lime", "medium": "orange", "low": "red"}
    for i, book in enumerate(books, 1):
        if len(book["box"]) != 4:
            continue
        x0, y0, x1, y1 = to_pixels(book["box"], w, h)
        draw.rectangle([x0, y0, x1, y1], outline=colours[book["confidence"]], width=4)
        draw.text((x0 + 4, y0 + 4), str(i), fill="yellow", font_size=28, stroke_width=2, stroke_fill="black")
        px, py = int((x1 - x0) * CROP_PAD), int((y1 - y0) * CROP_PAD)
        crop = img.crop((max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py)))
        crop.save(out / "crops" / f"{i:02d}.jpg", quality=85)
    annotated.save(out / "annotated.jpg", quality=85)
    (out / "books.json").write_text(json.dumps({"stats": stats, "books": books}, indent=2, ensure_ascii=False))


def main() -> None:
    load_dotenv(ROOT / ".env")
    register_heif_opener()
    client = anthropic.Anthropic()
    for arg in sys.argv[1:]:
        photo = Path(arg).expanduser()
        img = load_photo(photo)
        for model in MODELS:
            print(f"{photo.name} · {model} …", flush=True)
            books, stats = detect(client, model, img)
            save_outputs(img, books, stats, OUT / photo.stem / model)
            print(f"  {len(books)} books, {stats}")
            for i, b in enumerate(books, 1):
                print(f"  {i:2d}. [{b['confidence'][0]}] {b['title']} — {b['author']}")


if __name__ == "__main__":
    main()
