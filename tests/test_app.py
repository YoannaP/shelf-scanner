import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from shelf_scanner.app import create_app
from shelf_scanner.config import Settings
from shelf_scanner.detector import Detection
from shelf_scanner.lookup import BookInfo
from shelf_scanner.vault import Vault

TEMPLATE = """---
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
status: to-read
priority: false
---

## Reading notes
![[Reading Notes.base]]
"""

EXISTING = """---
categories:
  - "[[Books]]"
author:
  - "[[Peter Thiel]]"
via: "Goodreads"
status: read
---
"""


class FakeDetector:
    """Photo 1 has three books; photo 2 overlaps it (Alchemy again) and adds one."""

    def __init__(self):
        self.calls = 0

    def detect(self, img):
        self.calls += 1
        if self.calls == 1:
            return [
                Detection("Zero to One", "Peter Thiel", "high", [100, 100, 200, 900]),
                Detection("Alchemy", "Rory Sutherland", "medium", [300, 100, 350, 900]),
                Detection("", "", "low", [400, 100, 420, 900]),
            ]
        return [
            Detection("Alchemy", "", "high", [0, 100, 200, 900]),
            Detection("Outliers", "Malcolm Gladwell", "high", [500, 100, 600, 900]),
        ]


def fake_lookup(title, author):
    if title == "Obscure Pamphlet":
        return None
    return BookInfo(title, author or "Looked Up Author", 2015, 300, f"https://covers.example/{title}.jpg")


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "References").mkdir(parents=True)
    (vault / "Templates").mkdir()
    (vault / "Templates" / "Book Template.md").write_text(TEMPLATE)
    (vault / "References" / "Zero to One.md").write_text(EXISTING)
    settings = Settings(data_dir=tmp_path / "data", csv_path=tmp_path / "data" / "log.csv",
                        vault_path=vault, model="test")
    client = TestClient(create_app(settings, FakeDetector(), fake_lookup))
    return client, vault / "References", settings.csv_path


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "brown").save(buf, "JPEG")
    return buf.getvalue()


def make_scan(client, owned=True) -> dict:
    files = [("files", ("a.jpg", jpeg(), "image/jpeg")), ("files", ("b.jpg", jpeg(), "image/jpeg"))]
    scan = client.post("/api/scans", files=files, data={"owned": str(owned).lower()}).json()
    # TestClient runs background tasks before returning, so detection is done
    return client.get(f"/api/scans/{scan['id']}").json()


def by_title(scan, title):
    return next(b for b in scan["books"] if b["title"] == title)


def classify(client, scan, book, status):
    r = client.post(f"/api/scans/{scan['id']}/books/{book['id']}/classify", json={"status": status})
    assert r.status_code == 200, r.text
    return r.json()


def rows(csv_path: Path) -> list[dict]:
    return list(csv.DictReader(csv_path.open()))


def test_detection_dedupes_matches_vault_and_crops(env):
    client, refs, _ = env
    scan = make_scan(client)
    assert scan["state"] == "ready", scan["error"]
    titles = [b["title"] for b in scan["books"]]
    assert titles == ["Zero to One", "Alchemy", "", "Outliers"]  # Alchemy merged, shelf order kept

    alchemy = by_title(scan, "Alchemy")
    assert len(alchemy["seen_in"]) == 2
    assert alchemy["confidence"] == "high"  # clearer second sighting kept
    assert alchemy["author"] == "Rory Sutherland"
    assert alchemy["verified"] and alchemy["cover_url"]

    assert by_title(scan, "Zero to One")["vault_match"] == "Zero to One"
    assert by_title(scan, "Outliers")["vault_match"] is None
    assert all(b["crop"] for b in scan["books"])


def test_classify_writes_note_from_template_and_csv(env):
    client, refs, csv_path = env
    scan = make_scan(client)
    scan = classify(client, scan, by_title(scan, "Outliers"), "read")

    note = (refs / "Outliers.md").read_text()
    assert '  - "[[Malcolm Gladwell]]"' in note
    assert "\nstatus: read\n" in note
    assert "\nowned: true\n" in note
    assert '\nvia: "Shelf scan"\n' in note
    assert "\nyear: 2015\n" in note
    assert "cover: https://covers.example/Outliers.jpg" in note
    assert "## Reading notes" in note and "priority: false" in note
    assert "{{date}}" not in note

    [row] = rows(csv_path)
    assert row["title"] == "Outliers" and row["status"] == "read" and row["vault_note"] == "Outliers"
    assert row["source_photo"] == "b.jpg"


def test_reclassify_updates_note_and_appends_row(env):
    client, refs, csv_path = env
    scan = make_scan(client)
    book = by_title(scan, "Outliers")
    classify(client, scan, book, "read")
    scan = classify(client, scan, book, "not-finished")
    assert "\nstatus: not-finished\n" in (refs / "Outliers.md").read_text()
    assert len(list(refs.glob("Outliers*.md"))) == 1
    assert [r["status"] for r in rows(csv_path)] == ["read", "not-finished"]


def test_skip_records_owned_without_status(env):
    client, refs, csv_path = env
    scan = make_scan(client)
    classify(client, scan, by_title(scan, "Outliers"), "skipped")
    note = (refs / "Outliers.md").read_text()
    assert "\nstatus:\n" in note and "\nowned: true\n" in note
    assert rows(csv_path)[0]["status"] == "skipped"


def test_skip_on_someone_elses_shelf_writes_no_note(env):
    client, refs, csv_path = env
    scan = make_scan(client, owned=False)
    classify(client, scan, by_title(scan, "Outliers"), "to-read")
    assert "\nowned:\n" in (refs / "Outliers.md").read_text()
    classify(client, scan, by_title(scan, "Outliers"), "skipped")
    assert not (refs / "Outliers.md").exists()  # the app's own note is withdrawn
    assert [r["status"] for r in rows(csv_path)] == ["to-read", "skipped"]


def test_not_a_book_writes_nothing_and_can_be_restored(env):
    client, refs, csv_path = env
    scan = make_scan(client)
    unknown = by_title(scan, "")
    scan = classify(client, scan, unknown, "removed")
    assert by_title(scan, "")["status"] == "removed"
    assert not csv_path.exists()
    scan = classify(client, scan, unknown, None)
    assert by_title(scan, "")["status"] is None


def test_removing_a_classified_book_deletes_only_the_apps_note(env):
    client, refs, _ = env
    scan = make_scan(client)
    classify(client, scan, by_title(scan, "Outliers"), "read")
    classify(client, scan, by_title(scan, "Outliers"), "removed")
    assert not (refs / "Outliers.md").exists()
    # a note the app didn't write is never deleted
    assert not Vault(refs.parent).delete_app_note(refs / "Zero to One.md")
    assert (refs / "Zero to One.md").exists()


def test_unreadable_book_must_be_named_before_classifying(env):
    client, refs, _ = env
    scan = make_scan(client)
    unknown = by_title(scan, "")
    r = client.post(f"/api/scans/{scan['id']}/books/{unknown['id']}/classify", json={"status": "read"})
    assert r.status_code == 400

    scan = client.patch(f"/api/scans/{scan['id']}/books/{unknown['id']}",
                        json={"title": "Thinking, Fast and Slow", "author": "Daniel Kahneman"}).json()
    named = by_title(scan, "Thinking, Fast and Slow")
    assert named["verified"] and named["cover_url"]
    classify(client, scan, named, "read")
    assert (refs / "Thinking, Fast and Slow.md").exists()


def test_editing_a_classified_book_renames_its_note(env):
    client, refs, _ = env
    scan = make_scan(client)
    book = by_title(scan, "Outliers")
    classify(client, scan, book, "to-read")
    client.patch(f"/api/scans/{scan['id']}/books/{book['id']}", json={"title": "Outliers Revisited"})
    assert not (refs / "Outliers.md").exists()
    assert "\nstatus: to-read\n" in (refs / "Outliers Revisited.md").read_text()


def test_classify_anyway_overrides_a_wrong_vault_match(env):
    client, refs, _ = env
    scan = make_scan(client)
    zero = by_title(scan, "Zero to One")
    scan = client.patch(f"/api/scans/{scan['id']}/books/{zero['id']}", json={"classify_anyway": True}).json()
    classify(client, scan, by_title(scan, "Zero to One"), "read")
    assert (refs / "Zero to One (Peter Thiel).md").exists()
    assert "status: read" in (refs / "Zero to One.md").read_text()  # original untouched
    assert "Goodreads" in (refs / "Zero to One.md").read_text()


def test_add_missed_book(env):
    client, refs, _ = env
    scan = make_scan(client)
    scan = client.post(f"/api/scans/{scan['id']}/books", json={"title": "Obscure Pamphlet", "author": "Nobody"}).json()
    book = by_title(scan, "Obscure Pamphlet")
    assert book["manual"] and not book["verified"] and book["crop"] is None
    classify(client, scan, book, "read")
    assert "\ncover:\n" in (refs / "Obscure Pamphlet.md").read_text()


def test_csv_only_without_vault(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", csv_path=tmp_path / "log.csv", vault_path=None, model="test")
    client = TestClient(create_app(settings, FakeDetector(), fake_lookup))
    scan = make_scan(client)
    assert all(b["vault_match"] is None for b in scan["books"])
    classify(client, scan, by_title(scan, "Outliers"), "read")
    assert rows(settings.csv_path)[0]["vault_note"] == ""
    assert client.get("/api/config").json()["vault"] is None


def test_scan_list_summary(env):
    client, _, _ = env
    scan = make_scan(client)
    classify(client, scan, by_title(scan, "Outliers"), "read")
    [summary] = client.get("/api/scans").json()
    # Zero to One is already in the vault, so 3 active books
    assert summary["total"] == 3 and summary["classified"] == 1 and summary["photos"] == 2


def test_vault_match_rules(tmp_path):
    refs = tmp_path / "References"
    refs.mkdir()
    for name in ["1975 against method paul fayernabend - epistemic anarchy", "The Alchemy of Finance", "Theory of Everyone"]:
        (refs / f"{name}.md").write_text("---\nstatus: read\n---\n")
    vault = Vault(tmp_path)
    assert vault.match("Against Method").stem.startswith("1975 against method")
    assert vault.match("A Theory of Everyone").stem == "Theory of Everyone"
    assert vault.match("Alchemy") is None
    assert vault.match("Finance") is None
