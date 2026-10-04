"""Book detection. `Detector` is the seam: swap ClaudeDetector for local OCR or a local model."""

import base64
import json
from dataclasses import dataclass
from typing import Protocol

import anthropic
from PIL import Image

from .images import to_jpeg


@dataclass
class Detection:
    title: str
    author: str
    confidence: str  # high | medium | low
    box: list[int] | None  # [x0, y0, x1, y1], normalised 0-1000


class Detector(Protocol):
    def detect(self, img: Image.Image) -> list[Detection]: ...


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


def clean_box(box: list[int]) -> list[int] | None:
    if len(box) != 4:
        return None
    x0, y0, x1, y1 = (min(1000, max(0, v)) for v in box)
    return [x0, y0, x1, y1] if x1 > x0 and y1 > y0 else None


class ClaudeDetector:
    def __init__(self, model: str):
        self.model = model
        self._client: anthropic.Anthropic | None = None

    def detect(self, img: Image.Image) -> list[Detection]:
        self._client = self._client or anthropic.Anthropic()
        data = base64.standard_b64encode(to_jpeg(img)).decode()
        with self._client.beta.messages.stream(
            model=self.model,
            max_tokens=32000,
            # if a safety classifier declines, retry server-side on Anthropic's recommended fallback
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
            raise RuntimeError(f"The model declined to read this photo ({resp.stop_details}).")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("The model's answer was cut off; try a photo with fewer books.")
        text = next(b.text for b in resp.content if b.type == "text")
        return [
            Detection(b["title"].strip(), b["author"].strip(), b["confidence"], clean_box(b["box"]))
            for b in json.loads(text)["books"]
        ]
