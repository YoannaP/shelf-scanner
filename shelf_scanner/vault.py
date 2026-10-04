"""Read and write book notes in an Obsidian vault's References/ folder."""

import datetime
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from .text import norm, short_title

APP_VIA = "Shelf scan"
# app status -> note `status:` value (skipped = owned, no opinion)
NOTE_STATUS = {"read": "read", "to-read": "to-read", "not-finished": "not-finished", "skipped": ""}

FALLBACK_TEMPLATE = """---
categories:
  - "[[Books]]"
author: []
cover:
genre: []
pages:
year:
rating:
topics: []
created: {{date}}
last:
via: ""
format: []
owned:
started:
finished:
goodreads:
top-shelf:
status:
---
"""


@dataclass
class NoteData:
    title: str
    author: str
    cover_url: str | None = None
    year: int | None = None
    pages: int | None = None


def split_authors(author: str) -> list[str]:
    # quotes and brackets would break the quoted "[[wikilink]]" in YAML
    author = re.sub(r'["\[\]]', "", author)
    return [a.strip() for a in re.split(r"\s*(?:&|,|\band\b)\s*", author) if a.strip()]


def set_field(fm: str, key: str, value: str) -> str:
    # a value starting with a newline is a YAML list block (e.g. authors)
    line = f"{key}:{value}" if value.startswith("\n") else f"{key}: {value}".rstrip()
    pattern = rf"^{re.escape(key)}:[^\n]*(?:\n[ \t]+-[^\n]*)*"
    if re.search(pattern, fm, re.M):
        return re.sub(pattern, lambda _: line, fm, count=1, flags=re.M)
    return f"{fm}\n{line}"


def split_note(text: str) -> tuple[str, str]:
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        raise ValueError("note has no frontmatter")
    return m.group(1), m.group(2)


def join_note(fm: str, body: str) -> str:
    return f"---\n{fm}\n---\n{body}"


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.books = root / "References"
        self.template = root / "Templates" / "Book Template.md"

    @property
    def name(self) -> str:
        return self.root.name

    def index(self) -> dict[str, Path]:
        return {norm(p.stem): p for p in self.books.glob("*.md")}

    def match(self, title: str, index: dict[str, Path] | None = None) -> Path | None:
        """Existing note for this title: exact normalised name, else a very close one."""
        key = norm(title)
        if not key:
            return None
        index = self.index() if index is None else index
        if key in index:
            return index[key]
        close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.9)
        if close:
            return index[close[0]]
        # free-form names like "1975 against method paul fayernabend - epistemic anarchy";
        # only for multi-word titles, so "Alchemy" can't match "The Alchemy of Finance"
        if len([w for w in key.split() if w not in {"the", "a", "an"}]) >= 2:
            hits = [k for k in index if f" {key} " in f" {k} "]
            if hits:
                return index[min(hits, key=len)]
        return None

    def write_note(self, data: NoteData, status: str, owned: bool) -> Path:
        today = datetime.date.today().isoformat()
        text = self.template.read_text(encoding="utf-8") if self.template.exists() else FALLBACK_TEMPLATE
        fm, body = split_note(text.replace("{{date}}", today))
        authors = split_authors(data.author)
        fm = set_field(fm, "author", "\n".join(["", *(f'  - "[[{a}]]"' for a in authors)]) if authors else "[]")
        for key, value in {
            "cover": data.cover_url or "",
            "pages": data.pages or "",
            "year": data.year or "",
            "created": today,
            "via": f'"{APP_VIA}"',
            "owned": "true" if owned else "",
            "status": NOTE_STATUS[status],
        }.items():
            fm = set_field(fm, key, str(value))
        path = self._free_path(data)
        path.write_text(join_note(fm, body), encoding="utf-8")
        return path

    def set_status(self, path: Path, status: str) -> None:
        fm, body = split_note(path.read_text(encoding="utf-8"))
        path.write_text(join_note(set_field(fm, "status", NOTE_STATUS[status]), body), encoding="utf-8")

    def delete_app_note(self, path: Path) -> bool:
        """Delete a note only if this app created it (never touch the user's own notes)."""
        if not path.exists() or path.parent != self.books:
            return False
        fm, _ = split_note(path.read_text(encoding="utf-8"))
        if not re.search(rf'^via:\s*"?{APP_VIA}"?\s*$', fm, re.M):
            return False
        path.unlink()
        return True

    def _free_path(self, data: NoteData) -> Path:
        base = short_title(data.title) or "Untitled"
        candidates = [base]
        if authors := split_authors(data.author):
            candidates.append(f"{base} ({authors[0]})")
        candidates += [f"{base} {n}" for n in range(2, 100)]
        for name in candidates:
            path = self.books / f"{name}.md"
            if not path.exists():
                return path
        raise RuntimeError(f"no free note name for {base!r}")
