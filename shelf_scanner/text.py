"""Title normalisation, kept identical to goodreads-sync so both tools match notes the same way."""

import html
import re


def norm(s: str) -> str:
    s = html.unescape(s).lower().replace("’", "'")
    s = re.split(r"[:(]", s)[0]
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def short_title(t: str) -> str:
    t = html.unescape(t).strip()
    t = re.split(r"\s*[:(]\s*", t)[0].strip()
    return re.sub(r'[\\/#^\[\]|?*"<>]', "", t).strip()
