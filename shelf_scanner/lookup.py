"""Confirm a detected book exists and fetch its cover: Open Library first, then Google Books."""

import difflib
import os
from dataclasses import dataclass

import httpx

from .text import norm

HEADERS = {"User-Agent": "ShelfScanner/0.1 (personal book cataloguing)"}


@dataclass
class BookInfo:
    title: str
    author: str
    year: int | None
    pages: int | None
    cover_url: str | None


def titles_match(detected: str, found: str) -> bool:
    a, b = norm(detected), norm(found)
    if not a or not b:
        return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
        return True
    # "Zero to One" vs "Zero to One Notes on Startups", but not "Summary of ... Zero to One"
    return min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a))


def open_library(client: httpx.Client, title: str, author: str) -> BookInfo | None:
    params = {"title": title, "limit": 5,
              "fields": "title,author_name,first_publish_year,number_of_pages_median,cover_i"}
    if author:
        params["author"] = author
    r = client.get("https://openlibrary.org/search.json", params=params)
    r.raise_for_status()
    for doc in r.json().get("docs", []):
        if titles_match(title, doc.get("title", "")):
            cover = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg" if doc.get("cover_i") else None
            return BookInfo(doc["title"], ", ".join(doc.get("author_name", [])[:2]),
                            doc.get("first_publish_year"), doc.get("number_of_pages_median"), cover)
    return None


def google_books(client: httpx.Client, title: str, author: str) -> BookInfo | None:
    q = f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else "")
    params = {"q": q, "maxResults": 5, "printType": "books"}
    # without a key, Google's daily quota is shared by every anonymous caller and often exhausted
    if key := os.environ.get("GOOGLE_BOOKS_API_KEY"):
        params["key"] = key
    r = client.get("https://www.googleapis.com/books/v1/volumes", params=params)
    r.raise_for_status()
    for item in r.json().get("items", []):
        v = item.get("volumeInfo", {})
        if titles_match(title, v.get("title", "")):
            links = v.get("imageLinks", {})
            cover = links.get("thumbnail") or links.get("smallThumbnail")
            if cover:
                cover = cover.replace("http://", "https://").replace("&edge=curl", "")
            date = v.get("publishedDate", "")[:4]
            return BookInfo(v.get("title", title), ", ".join(v.get("authors", [])[:2]),
                            int(date) if date.isdigit() else None, v.get("pageCount"), cover)
    return None


def lookup(title: str, author: str) -> BookInfo | None:
    """Best verified match, preferring one with a cover. None if neither source knows the book."""
    if not title.strip():
        return None
    best = None
    with httpx.Client(timeout=15, headers=HEADERS, follow_redirects=True) as client:
        for source in (open_library, google_books):
            info = None
            for _attempt in range(2):  # one retry: Open Library is often slow under load
                try:
                    info = source(client, title, author)
                    break
                except (httpx.HTTPError, ValueError):
                    continue
            if info and info.cover_url:
                return info
            best = best or info
    return best
