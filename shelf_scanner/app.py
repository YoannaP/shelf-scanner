import os
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import BaseModel

from .config import Settings, load_settings
from .detector import ClaudeDetector, Detector
from .lookup import lookup
from .service import LookupFn, Scanner
from .store import Store
from .vault import Vault

STATIC = Path(__file__).parent / "static"


class ClassifyBody(BaseModel):
    status: str | None


class EditBody(BaseModel):
    title: str | None = None
    author: str | None = None
    classify_anyway: bool | None = None


class AddBody(BaseModel):
    title: str
    author: str = ""


def make_vault(settings: Settings) -> Vault | None:
    if not settings.vault_path:
        return None
    vault = Vault(settings.vault_path)
    if not vault.books.is_dir():
        print(f"VAULT_PATH has no References/ folder ({vault.books}); running CSV-only.", file=sys.stderr)
        return None
    return vault


def create_app(settings: Settings | None = None, detector: Detector | None = None,
               lookup_fn: LookupFn = lookup) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.data_dir / "scans")
    vault = make_vault(settings)
    scanner = Scanner(store, detector or ClaudeDetector(settings.model), lookup_fn, vault, settings.csv_path)
    app = FastAPI(title="Shelf Scanner")

    def load(scan_id: str):
        try:
            return store.load(scan_id)
        except KeyError:
            raise HTTPException(404, "Scan not found")

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, "Not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/config")
    def config():
        return {"vault": vault.name if vault else None, "model": settings.model,
                "csv": str(settings.csv_path)}

    @app.get("/api/scans")
    def list_scans():
        return [s.summary() for s in store.all()]

    @app.post("/api/scans")
    async def create_scan(background: BackgroundTasks, files: list[UploadFile] = File(...),
                          owned: bool = Form(True)):
        uploads = [(f.filename or "photo", await f.read()) for f in files]
        try:
            scan = scanner.create(uploads, owned)
        except UnidentifiedImageError:
            raise HTTPException(400, "One of the files isn't an image this app can read.")
        background.add_task(scanner.process, scan.id)
        return scan

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: str):
        return load(scan_id)

    @app.post("/api/scans/{scan_id}/books/{book_id}/classify")
    def classify(scan_id: str, book_id: str, body: ClassifyBody):
        return guard(scanner.classify, scan_id, book_id, body.status)

    @app.patch("/api/scans/{scan_id}/books/{book_id}")
    def edit(scan_id: str, book_id: str, body: EditBody):
        return guard(scanner.edit, scan_id, book_id, body.title, body.author, body.classify_anyway)

    @app.post("/api/scans/{scan_id}/books")
    def add(scan_id: str, body: AddBody):
        return guard(scanner.add, scan_id, body.title, body.author)

    app.mount("/files", StaticFiles(directory=store.root), name="files")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", 8765))
    print(f"Shelf Scanner running → http://localhost:{port}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
