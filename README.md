# Shelf Scanner

Photograph a bookshelf, get a list of the books on it, then classify each one as
Read / Want to Read / Did not finish / Skip. Choices are written to an Obsidian vault
and logged to an append-only CSV. See `SPEC.md` for the full behaviour.

## Run

```sh
uv run shelf-scanner        # → http://localhost:8765
```

## Configure (`.env`)

| Variable | |
|---|---|
| `ANTHROPIC_API_KEY` | required; reads the spines (copy `.env.example` to `.env`) |
| `VAULT_PATH` | Obsidian vault with `References/` (and optionally `Templates/Book Template.md`). Unset = CSV only |
| `MODEL` | default `claude-sonnet-5-5`; `claude-opus-5-5` flags more unreadable spines |
| `GOOGLE_BOOKS_API_KEY` | optional; the anonymous Google Books quota is often exhausted |
| `DATA_DIR`, `CSV_PATH`, `PORT` | defaults: `data/`, `data/classifications.csv`, `8765` |

## Keys (Run-through)

`1` Read · `2` Want to Read · `3` Did not finish · `4` Skip · `←` Back · `E` Edit · `Del` Not a book

## Vault safety

Books matching an existing note are never touched. The app only edits or deletes notes it
created itself (`via: "Shelf scan"`).

## Tests

```sh
uv run --group dev pytest
```
