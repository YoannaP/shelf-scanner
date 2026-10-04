# Shelf Scanner — Spec v1

Detect books from photos of a bookshelf, then classify each one as Read / Want to Read / Did not finish / Skip. Results go to an Obsidian vault and an append-only CSV.

## Context
- Personal tool; may be open-sourced later.
- v1 runs on the Mac browser at localhost. An iPhone version may follow, so keep the UI responsive, tap-friendly and API-first.
- Stack: Python (FastAPI) + plain HTML/JS. Start with one `uv run` command.
- `.env`: `ANTHROPIC_API_KEY`, `VAULT_PATH` (optional — unset means CSV-only), `MODEL` (default Sonnet 5.5).

## Flow
1. **Home**: scan history (date, photo count, "34/60 classified") with **New scan** at the top. Scans are saved on disk and can be reopened.
2. **New scan**: upload one or more photos (a wide or tall shelf can be split across several). HEIC is converted to JPEG and downscaled to ~2000px. Toggle **"These are my books"** (default on).
3. **Detect**: Claude vision returns title, author and an approximate spine box per book, behind a swappable detector interface.
   - Look each book up in Open Library or Google Books for a cover, year and pages. No match → ⚠️ not verified.
   - The same book in several photos is merged ("seen in N photos"), keeping the clearest crop.
   - Fuzzy-match against vault `References/` notes. Matches are left out of the queue and never modified.
4. **Two switchable views**:
   - **List**: photos with numbered boxes, plus every detected book. Edit title or author, add missed books, 🗑 remove, set or change status per row. Filter chips: All / Unclassified / Read / Want / DNF / Skipped. A collapsed **"Already in vault (N)"** section links each match to its note and has an "Actually, classify this" button.
   - **Run-through**: one card per **unclassified** book, in shelf order (photo by photo, left to right). Card = padded spine crop + cover; manually added books show the cover only. Resumes at the next unclassified book. Clicking a book in the List opens its card.

## Classification
| Button | Key | Vault `status` | CSV status |
|---|---|---|---|
| Read | `1` | `read` | read |
| Want to Read | `2` | `to-read` | to-read |
| Did not finish | `3` | `not-finished` | not-finished |
| Skip | `4` | *(empty)* | skipped |
| 🗑 Not a book | `Del` | nothing | nothing |

Other keys: `←` Back (reclassifying rewrites the note status and appends a new CSV row) and `E` Edit.

## Writes (on every click)
- **Vault note** in `References/`, named with the short title (subtitle removed). Same format as goodreads-sync:
  - Fields: `author` (wikilinks), `cover` URL, `year`, `pages`, `via: "Shelf scan"`, `created`, `status`, plus `owned: true` when the toggle is on.
  - Body from `Templates/Book Template.md`.
  - With the toggle off, Skip writes no note.
- **CSV**: one master `classifications.csv`, append-only. Columns: `timestamp, title, author, status, owned, cover_url, vault_note, source_photo`.

## Out of scope for v1
Video input, iPhone app, writing to Goodreads, drag-to-adjust spine boxes (add only if padded crops prove poor).

## Build order
1. Prototype (`prototype/`): run real shelf photos through Sonnet 5.5 and Opus 5.5 to check spine-reading accuracy and crop quality.
2. Full app.
