import csv
from pathlib import Path

COLUMNS = ["timestamp", "title", "author", "status", "owned", "cover_url", "vault_note", "source_photo"]


def append(path: Path, row: dict) -> None:
    """Append-only history: every click is a new row, reclassifications included."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)
