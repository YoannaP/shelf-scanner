"""Scan sessions on disk: data/scans/<id>/{scan.json, photos/, crops/}."""

import threading
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Status = Literal["read", "to-read", "not-finished", "skipped", "removed"]


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Photo(BaseModel):
    id: str
    filename: str
    width: int
    height: int


class Book(BaseModel):
    id: str
    title: str
    author: str = ""
    confidence: str = "high"
    photo_id: str | None = None
    box: list[int] | None = None
    crop: str | None = None
    seen_in: list[str] = []
    cover_url: str | None = None
    year: int | None = None
    pages: int | None = None
    verified: bool = False
    vault_match: str | None = None  # name of an existing note this book matched
    classify_anyway: bool = False  # user overrode a (wrong) vault match
    status: Status | None = None
    note_path: str | None = None  # note this app wrote for the book
    manual: bool = False

    @property
    def active(self) -> bool:
        """Belongs in the classification queue / main list."""
        return self.status != "removed" and (self.vault_match is None or self.classify_anyway)


class Scan(BaseModel):
    id: str
    created: str
    owned: bool = True
    state: Literal["detecting", "ready", "error"] = "detecting"
    progress: str = ""
    error: str | None = None
    photos: list[Photo] = []
    books: list[Book] = []

    def book(self, book_id: str) -> Book:
        for b in self.books:
            if b.id == book_id:
                return b
        raise KeyError(book_id)

    def summary(self) -> dict:
        active = [b for b in self.books if b.active]
        return {
            "id": self.id, "created": self.created, "owned": self.owned, "state": self.state,
            "photos": len(self.photos), "total": len(active),
            "classified": sum(b.status is not None for b in active),
            "thumb": self.photos[0].id if self.photos else None,
        }


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        # one lock for every read-modify-write; a single local user never contends for long
        self.lock = threading.RLock()

    def dir(self, scan_id: str) -> Path:
        if not scan_id.isalnum():
            raise KeyError(scan_id)
        return self.root / scan_id

    def load(self, scan_id: str) -> Scan:
        path = self.dir(scan_id) / "scan.json"
        if not path.exists():
            raise KeyError(scan_id)
        return Scan.model_validate_json(path.read_text())

    def save(self, scan: Scan) -> None:
        d = self.dir(scan.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "scan.json.tmp"
        tmp.write_text(scan.model_dump_json(indent=2))
        tmp.replace(d / "scan.json")

    def all(self) -> list[Scan]:
        scans = [self.load(p.parent.name) for p in self.root.glob("*/scan.json")]
        return sorted(scans, key=lambda s: s.created, reverse=True)
