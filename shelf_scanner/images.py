import io

from PIL import Image, ImageDraw, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

MAX_EDGE = 2000  # plenty for reading spines; keeps API cost down
CROP_PAD = 0.15  # detection boxes are approximate, so crop generously
MIN_ASPECT = 0.3  # minimum crop width / height


def open_photo(data: bytes) -> Image.Image:
    """Decode any upload (HEIC included), apply EXIF rotation, downscale."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    return img


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def crop_spine(img: Image.Image, box: list[int]) -> Image.Image:
    """Crop a 0-1000 normalised box with padding, outlining the book itself.

    Thin spines are widened (to MIN_ASPECT of the height) so the neighbouring
    books give enough context to recognise which one it is on your shelf.
    """
    w, h = img.size
    x0, y0, x1, y1 = (box[0] * w // 1000, box[1] * h // 1000, box[2] * w // 1000, box[3] * h // 1000)
    px, py = int((x1 - x0) * CROP_PAD), int((y1 - y0) * CROP_PAD)
    cx0, cy0, cx1, cy1 = x0 - px, y0 - py, x1 + px, y1 + py
    if (short := int((cy1 - cy0) * MIN_ASPECT) - (cx1 - cx0)) > 0:
        cx0, cx1 = cx0 - short // 2, cx1 + short - short // 2
    cx0, cy0, cx1, cy1 = max(0, cx0), max(0, cy0), min(w, cx1), min(h, cy1)
    crop = img.crop((cx0, cy0, cx1, cy1))
    line = max(2, crop.height // 200)
    ImageDraw.Draw(crop).rectangle([x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0], outline=(255, 214, 10), width=line)
    return crop
