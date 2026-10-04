"""Scan lifecycle: detect -> dedupe -> look up -> match vault, then classify/edit/add books."""

import datetime
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import csvlog
from .detector import Detector
from .images import crop_spine, open_photo, to_jpeg
from .lookup import BookInfo
from .store import Book, Photo, Scan, Store, new_id
from .text import norm
from .vault import NoteData, Vault

CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}
NOTE_STATUSES = {"read", "to-read", "not-finished", "skipped"}

LookupFn = Callable[[str, str], BookInfo | None]


def box_area(b: Book) -> int:
    return (b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) if b.box else 0


def same_book(a: Book, b: Book) -> bool:
    if not norm(a.title) or norm(a.title) != norm(b.title):
        return False
    return not a.author or not b.author or norm(a.author) == norm(b.author)


def dedupe(books: list[Book]) -> list[Book]:
    """Merge a book seen in several (overlapping) photos, keeping its clearest crop."""
    out: list[Book] = []
    for b in books:
        dup = next((o for o in out if same_book(o, b)), None)
        if dup is None:
            out.append(b)
            continue
        dup.seen_in += [p for p in b.seen_in if p not in dup.seen_in]
        dup.author = dup.author or b.author
        if (CONFIDENCE_RANK[b.confidence], box_area(b)) > (CONFIDENCE_RANK[dup.confidence], box_area(dup)):
            dup.photo_id, dup.box, dup.crop, dup.confidence = b.photo_id, b.box, b.crop, b.confidence
    return out


class Scanner:
    def __init__(self, store: Store, detector: Detector, lookup: LookupFn,
                 vault: Vault | None, csv_path: Path):
        self.store = store
        self.detector = detector
        self.lookup = lookup
        self.vault = vault
        self.csv_path = csv_path

    # --- creating a scan -------------------------------------------------

    def create(self, uploads: list[tuple[str, bytes]], owned: bool) -> Scan:
        scan = Scan(id=new_id(), created=datetime.datetime.now().isoformat(timespec="seconds"), owned=owned)
        photos_dir = self.store.dir(scan.id) / "photos"
        photos_dir.mkdir(parents=True)
        for filename, data in uploads:
            img = open_photo(data)
            photo = Photo(id=new_id(), filename=filename, width=img.width, height=img.height)
            (photos_dir / f"{photo.id}.jpg").write_bytes(to_jpeg(img))
            scan.photos.append(photo)
        scan.progress = "Waiting to start…"
        self.store.save(scan)
        return scan

    def process(self, scan_id: str) -> None:
        """Runs in the background after upload; the UI polls the scan's state."""
        scan = self.store.load(scan_id)
        d = self.store.dir(scan_id)
        (d / "crops").mkdir(exist_ok=True)
        try:
            books: list[Book] = []
            for i, photo in enumerate(scan.photos, 1):
                self._progress(scan, f"Reading spines in photo {i} of {len(scan.photos)}…")
                img = Image.open(d / "photos" / f"{photo.id}.jpg")
                for det in self.detector.detect(img):
                    book = Book(id=new_id(), title=det.title, author=det.author, confidence=det.confidence,
                                photo_id=photo.id, box=det.box, seen_in=[photo.id])
                    if det.box:
                        book.crop = f"{book.id}.jpg"
                        (d / "crops" / book.crop).write_bytes(to_jpeg(crop_spine(img, det.box), 85))
                    books.append(book)
            books = dedupe(books)
            self._progress(scan, f"Looking up {len(books)} books…")
            with ThreadPoolExecutor(4) as pool:  # stay polite to Open Library
                list(pool.map(self._enrich, books))
            index = self.vault.index() if self.vault else None
            for b in books:
                self._match_vault(b, index)
            scan.books, scan.state, scan.progress = books, "ready", ""
        except Exception as e:  # surface any failure in the UI rather than hanging on "detecting"
            scan.state, scan.error = "error", f"{type(e).__name__}: {e}"
        self.store.save(scan)

    def _progress(self, scan: Scan, msg: str) -> None:
        scan.progress = msg
        self.store.save(scan)

    def _enrich(self, book: Book) -> None:
        try:
            info = self.lookup(book.title, book.author)
        except Exception:
            info = None
        self._apply_info(book, info)

    @staticmethod
    def _apply_info(book: Book, info: BookInfo | None) -> None:
        book.verified = info is not None
        book.cover_url = info.cover_url if info else None
        book.year = info.year if info else None
        book.pages = info.pages if info else None
        if info and not book.author:
            book.author = info.author

    def _match_vault(self, book: Book, index=None) -> None:
        match = self.vault.match(book.title, index) if self.vault else None
        book.vault_match = match.stem if match else None

    # --- classifying ----------------------------------------------------

    def classify(self, scan_id: str, book_id: str, status: str | None) -> Scan:
        with self.store.lock:
            scan = self.store.load(scan_id)
            book = scan.book(book_id)
            if status is None:  # restore a removed book to the queue
                if book.status != "removed":
                    raise ValueError("Only removed books can be restored")
                book.status = None
            elif status == "removed":
                self._drop_note(book)
                book.status = "removed"
            elif status in NOTE_STATUSES:
                if not book.title.strip():
                    raise ValueError("Give this book a title first")
                self._write_note(scan, book, status)
                book.status = status
                photo = next((p for p in scan.photos if p.id == book.photo_id), None)
                csvlog.append(self.csv_path, {
                    "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                    "title": book.title, "author": book.author, "status": status,
                    "owned": scan.owned, "cover_url": book.cover_url or "",
                    "vault_note": Path(book.note_path).stem if book.note_path else "",
                    "source_photo": photo.filename if photo else "",
                })
            else:
                raise ValueError(f"Unknown status {status!r}")
            self.store.save(scan)
            return scan

    def _write_note(self, scan: Scan, book: Book, status: str) -> None:
        if not self.vault:
            return
        # Skip on a shelf that isn't yours has nothing worth recording
        if status == "skipped" and not scan.owned:
            self._drop_note(book)
            return
        existing = Path(book.note_path) if book.note_path else None
        if existing and existing.exists():
            self.vault.set_status(existing, status)
        else:
            data = NoteData(book.title, book.author, book.cover_url, book.year, book.pages)
            book.note_path = str(self.vault.write_note(data, status, scan.owned))

    def _drop_note(self, book: Book) -> None:
        if self.vault and book.note_path:
            self.vault.delete_app_note(Path(book.note_path))
        book.note_path = None

    # --- fixing detections ----------------------------------------------

    def edit(self, scan_id: str, book_id: str, title: str | None = None, author: str | None = None,
             classify_anyway: bool | None = None) -> Scan:
        with self.store.lock:
            book = self.store.load(scan_id).book(book_id)
        title = book.title if title is None else title.strip()
        author = book.author if author is None else author.strip()
        renamed = (title, author) != (book.title, book.author)
        info = self.lookup(title, author) if renamed else None  # network call outside the lock

        with self.store.lock:
            scan = self.store.load(scan_id)
            book = scan.book(book_id)
            if classify_anyway is not None:
                book.classify_anyway = classify_anyway
            if renamed:
                had_note = book.note_path is not None
                self._drop_note(book)  # its file name and fields are now wrong
                book.title, book.author = title, author
                self._apply_info(book, info)
                if not book.classify_anyway:
                    self._match_vault(book)
                if had_note and book.active and book.status in NOTE_STATUSES:
                    self._write_note(scan, book, book.status)
            self.store.save(scan)
            return scan

    def add(self, scan_id: str, title: str, author: str) -> Scan:
        title, author = title.strip(), author.strip()
        if not title:
            raise ValueError("Title is required")
        book = Book(id=new_id(), title=title, author=author, manual=True)
        self._enrich(book)
        self._match_vault(book)
        with self.store.lock:
            scan = self.store.load(scan_id)
            scan.books.append(book)
            self.store.save(scan)
            return scan
