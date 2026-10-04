import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    csv_path: Path
    vault_path: Path | None  # None = CSV-only
    model: str


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data")).expanduser()
    vault = os.environ.get("VAULT_PATH", "").strip()
    return Settings(
        data_dir=data_dir,
        csv_path=Path(os.environ.get("CSV_PATH", data_dir / "classifications.csv")).expanduser(),
        vault_path=Path(vault).expanduser() if vault else None,
        model=os.environ.get("MODEL", "claude-sonnet-5-5"),
    )
