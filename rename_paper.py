#!/usr/bin/env python3
"""
rename_paper.py — Rename research-paper PDFs to "YEAR_Author_Journal.pdf"

Tries many independent strategies, most-reliable first, and merges whatever
each one finds until all three fields (author, year, journal) are filled:

  1. DOI found in text            -> Crossref lookup by DOI       (best)
  2. arXiv ID found in text       -> arXiv API
  3. PMID found in text           -> PubMed ESummary API
  4. PDF embedded document info   -> Author/Title/CreationDate fields
  5. Title guessed by font size   -> Crossref bibliographic search
  6. Title guessed by font size   -> OpenAlex search (different index/coverage)
  7. ISSN found in text           -> Crossref journal-name lookup (fills journal only)
  8. Crude text heuristics        -> last resort, flagged as low-confidence

Each strategy only fills in fields that are still missing, so results from
multiple sources combine into the most complete answer available.

Usage:
  python3 rename_paper.py paper1.pdf paper2.pdf
  python3 rename_paper.py /path/to/folder/            # batch, recursive
  python3 rename_paper.py --dry-run /path/to/folder/  # preview only
  python3 rename_paper.py --copy /path/to/folder/      # copy instead of rename
  python3 rename_paper.py --email you@example.com file.pdf   # polite-pool API access (faster/more reliable)

Dependencies:
  pip install pdfplumber requests pypdf --break-system-packages
  (pypdf is optional — used only for embedded-metadata extraction; the
   script still works without it, just with one fewer strategy.)
"""

import argparse
import re
import shutil
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Install with:\n"
              "  pip install pdfplumber requests pypdf --break-system-packages")

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Install with:\n"
              "  pip install pdfplumber requests pypdf --break-system-packages")

try:
    from pypdf import PdfReader
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False


DOI_RE = re.compile(r'\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+', re.IGNORECASE)
YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')
ARXIV_RE = re.compile(r'\barXiv:\s*(\d{4}\.\d{4,5})(v\d+)?', re.IGNORECASE)
PMID_RE = re.compile(r'\bPMID:?\s*(\d{6,9})\b', re.IGNORECASE)
ISSN_RE = re.compile(r'\bISSN:?\s*(\d{4}-\d{3}[\dX])\b', re.IGNORECASE)

EMPTY_META = {"author": None, "year": None, "journal": None}
API_EMAIL = "example@example.com"  # overridden by --email; improves API rate limits


# --------------------------------------------------------------------------
# Low-level extraction helpers
# --------------------------------------------------------------------------

def extract_first_pages_text(pdf_path: Path, max_pages: int = 3) -> str:
    text_chunks = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:max_pages]:
                t = page.extract_text() or ""
                text_chunks.append(t)
    except Exception as e:
        print(f"  [warn] could not read text from {pdf_path.name}: {e}")
    return "\n".join(text_chunks)


def extract_title_by_fontsize(pdf_path: Path) -> Optional[str]:
    """Guess the paper title from the largest cluster of large-font text
    near the top of page 1 (titles are almost always the biggest text)."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            chars = page.chars
            if not chars:
                return None

            sizes = [round(c["size"]) for c in chars if c.get("size")]
            if not sizes:
                return None
            max_size = max(sizes)
            if max_size < 9:
                return None

            page_height = page.height
            title_chars = [
                c for c in chars
                if c.get("size") and round(c["size"]) >= max_size - 1
                and c["top"] < page_height * 0.6
            ]
            if not title_chars:
                return None

            title_chars.sort(key=lambda c: (round(c["top"]), c["x0"]))
            lines = {}
            for c in title_chars:
                key = round(c["top"] / 3)
                lines.setdefault(key, []).append(c)

            ordered_keys = sorted(lines.keys())[:4]
            title = ""
            for k in ordered_keys:
                line_chars = sorted(lines[k], key=lambda c: c["x0"])
                title += "".join(ch["text"] for ch in line_chars) + " "

            title = re.sub(r'\s+', ' ', title).strip()
            if 15 <= len(title) <= 300 and sum(c.isalpha() for c in title) > len(title) * 0.5:
                return title
    except Exception as e:
        print(f"  [warn] title extraction failed: {e}")
    return None


def get_docinfo(pdf_path: Path):
    if not HAVE_PYPDF:
        return None
    try:
        reader = PdfReader(str(pdf_path))
        return reader.metadata
    except Exception:
        return None


def title_similarity(a: str, b: str) -> float:
    norm = lambda s: re.sub(r'[^a-z0-9 ]', '', s.lower())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def merge_missing(target: dict, source: dict) -> None:
    for key in ("author", "year", "journal"):
        if not target.get(key) and source.get(key):
            target[key] = str(source[key])


def is_complete(meta: dict) -> bool:
    return all(meta.get(k) for k in ("author", "year", "journal"))


# --------------------------------------------------------------------------
# Strategy 1: DOI -> Crossref
# --------------------------------------------------------------------------

def find_doi(text: str) -> Optional[str]:
    match = DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip('.,;)>]')


def strategy_doi_crossref(text: str, pdf_path: Path) -> dict:
    doi = find_doi(text)
    if not doi:
        return dict(EMPTY_META)
    print(f"  [doi]      found DOI: {doi}")
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": f"rename_paper.py/2.0 (mailto:{API_EMAIL})"
        })
        if resp.status_code != 200:
            print(f"  [doi]      Crossref returned {resp.status_code}")
            return dict(EMPTY_META)
        msg = resp.json().get("message", {})
    except Exception as e:
        print(f"  [doi]      lookup failed: {e}")
        return dict(EMPTY_META)

    return _crossref_msg_to_meta(msg)


def _crossref_msg_to_meta(msg: dict) -> dict:
    author = None
    authors = msg.get("author") or []
    if authors:
        author = authors[0].get("family") or authors[0].get("name")

    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        date_parts = msg.get(key, {}).get("date-parts")
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
            break

    journal = None
    container = msg.get("container-title")
    if container:
        journal = container[0]
    elif msg.get("publisher"):
        journal = msg.get("publisher")

    meta = {"author": author, "year": year, "journal": journal}
    if any(meta.values()):
        print(f"  [doi]      -> {meta}")
    return meta


# --------------------------------------------------------------------------
# Strategy 2: arXiv ID -> arXiv API
# --------------------------------------------------------------------------

def strategy_arxiv(text: str, pdf_path: Path) -> dict:
    match = ARXIV_RE.search(text) or ARXIV_RE.search(pdf_path.name)
    if not match:
        return dict(EMPTY_META)
    arxiv_id = match.group(1)
    print(f"  [arxiv]    found arXiv ID: {arxiv_id}")
    try:
        resp = requests.get(
            "http://export.arxiv.org/api/query",
            params={"id_list": arxiv_id},
            timeout=10,
        )
        if resp.status_code != 200:
            return dict(EMPTY_META)
        xml = resp.text
    except Exception as e:
        print(f"  [arxiv]    lookup failed: {e}")
        return dict(EMPTY_META)

    author = None
    name_match = re.search(r'<author>\s*<name>(.*?)</name>', xml, re.DOTALL)
    if name_match:
        full_name = name_match.group(1).strip()
        author = full_name.split()[-1] if full_name else None

    year = None
    date_match = re.search(r'<published>(\d{4})-', xml)
    if date_match:
        year = date_match.group(1)

    # if it lists a formal journal-ref, prefer that; else label as arXiv preprint
    journal = "arXiv preprint"
    jref_match = re.search(r'<arxiv:journal_ref>(.*?)</arxiv:journal_ref>', xml, re.DOTALL)
    if jref_match:
        journal = jref_match.group(1).strip()[:100]

    meta = {"author": author, "year": year, "journal": journal}
    print(f"  [arxiv]    -> {meta}")
    return meta


# --------------------------------------------------------------------------
# Strategy 3: PMID -> PubMed ESummary
# --------------------------------------------------------------------------

def strategy_pubmed(text: str, pdf_path: Path) -> dict:
    match = PMID_RE.search(text)
    if not match:
        return dict(EMPTY_META)
    pmid = match.group(1)
    print(f"  [pubmed]   found PMID: {pmid}")
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "json"},
            timeout=10,
        )
        if resp.status_code != 200:
            return dict(EMPTY_META)
        data = resp.json()
        record = data.get("result", {}).get(pmid, {})
    except Exception as e:
        print(f"  [pubmed]   lookup failed: {e}")
        return dict(EMPTY_META)

    if not record:
        return dict(EMPTY_META)

    author = None
    authors = record.get("authors") or []
    if authors:
        full_name = authors[0].get("name", "")
        author = full_name.split()[0] if full_name else None  # PubMed gives "Surname AB"

    year = None
    pubdate = record.get("pubdate", "")
    year_match = YEAR_RE.search(pubdate)
    if year_match:
        year = year_match.group(0)

    journal = record.get("fulljournalname") or record.get("source")

    meta = {"author": author, "year": year, "journal": journal}
    print(f"  [pubmed]   -> {meta}")
    return meta


# --------------------------------------------------------------------------
# Strategy 4: PDF embedded document info
# --------------------------------------------------------------------------

def strategy_docinfo(text: str, pdf_path: Path) -> dict:
    info = get_docinfo(pdf_path)
    if not info:
        return dict(EMPTY_META)

    author = None
    raw_author = info.get("/Author") if hasattr(info, "get") else None
    if raw_author:
        # docinfo author fields are often "Smith, John; Doe, Jane" or "John Smith"
        first = re.split(r'[;,&]| and ', raw_author)[0].strip()
        parts = first.split()
        if parts:
            author = parts[-1]

    year = None
    raw_date = info.get("/CreationDate") if hasattr(info, "get") else None
    if raw_date:
        year_match = YEAR_RE.search(str(raw_date))
        if year_match:
            year = year_match.group(0)

    meta = {"author": author, "year": year, "journal": None}
    if any(meta.values()):
        print(f"  [docinfo]  -> {meta}")
    return meta


# --------------------------------------------------------------------------
# Strategy 5 & 6: Title -> Crossref / OpenAlex bibliographic search
# --------------------------------------------------------------------------

def strategy_title_crossref(text: str, pdf_path: Path, title_cache: dict) -> dict:
    title = title_cache.get("title")
    if not title:
        return dict(EMPTY_META)
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 3},
            timeout=10,
            headers={"User-Agent": f"rename_paper.py/2.0 (mailto:{API_EMAIL})"},
        )
        if resp.status_code != 200:
            return dict(EMPTY_META)
        items = resp.json().get("message", {}).get("items", [])
    except Exception as e:
        print(f"  [title/crossref] search failed: {e}")
        return dict(EMPTY_META)

    best, best_score = None, 0.0
    for item in items:
        cand_title = (item.get("title") or [None])[0]
        if not cand_title:
            continue
        score = title_similarity(title, cand_title)
        if score > best_score:
            best, best_score = item, score

    if not best or best_score < 0.55:
        return dict(EMPTY_META)

    print(f"  [title/crossref] matched (similarity {best_score:.2f}): "
          f"\"{(best.get('title') or [''])[0]}\"")
    return _crossref_msg_to_meta(best)


def strategy_title_openalex(text: str, pdf_path: Path, title_cache: dict) -> dict:
    title = title_cache.get("title")
    if not title:
        return dict(EMPTY_META)
    try:
        resp = requests.get(
            "https://api.openalex.org/works",
            params={"search": title, "per-page": 3, "mailto": API_EMAIL},
            timeout=10,
        )
        if resp.status_code != 200:
            return dict(EMPTY_META)
        items = resp.json().get("results", [])
    except Exception as e:
        print(f"  [title/openalex] search failed: {e}")
        return dict(EMPTY_META)

    best, best_score = None, 0.0
    for item in items:
        cand_title = item.get("display_name") or item.get("title")
        if not cand_title:
            continue
        score = title_similarity(title, cand_title)
        if score > best_score:
            best, best_score = item, score

    if not best or best_score < 0.55:
        return dict(EMPTY_META)

    print(f"  [title/openalex] matched (similarity {best_score:.2f}): "
          f"\"{best.get('display_name')}\"")

    author = None
    authorships = best.get("authorships") or []
    if authorships:
        author_obj = authorships[0].get("author", {})
        full_name = author_obj.get("display_name", "")
        if full_name:
            author = full_name.split()[-1]

    year = best.get("publication_year")
    journal = None
    host_venue = best.get("primary_location", {}).get("source") or {}
    journal = host_venue.get("display_name")

    return {"author": author, "year": year, "journal": journal}


# --------------------------------------------------------------------------
# Strategy 7: ISSN -> Crossref journal-name lookup (fills journal only)
# --------------------------------------------------------------------------

def strategy_issn(text: str, pdf_path: Path) -> dict:
    match = ISSN_RE.search(text)
    if not match:
        return dict(EMPTY_META)
    issn = match.group(1)
    print(f"  [issn]     found ISSN: {issn}")
    try:
        resp = requests.get(
            f"https://api.crossref.org/journals/{issn}",
            timeout=10,
            headers={"User-Agent": f"rename_paper.py/2.0 (mailto:{API_EMAIL})"},
        )
        if resp.status_code != 200:
            return dict(EMPTY_META)
        title = resp.json().get("message", {}).get("title")
    except Exception as e:
        print(f"  [issn]     lookup failed: {e}")
        return dict(EMPTY_META)

    meta = {"author": None, "year": None, "journal": title}
    if title:
        print(f"  [issn]     -> journal: {title}")
    return meta


# --------------------------------------------------------------------------
# Strategy 8: crude text heuristics (last resort)
# --------------------------------------------------------------------------

def strategy_heuristic(text: str, pdf_path: Path) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    year_match = YEAR_RE.search(text)
    year = year_match.group(0) if year_match else None

    author = None
    for line in lines[:15]:
        if re.search(r'\d', line):
            continue
        words = line.split()
        cap_words = [w for w in words if w[:1].isupper()]
        if 1 <= len(words) <= 8 and len(cap_words) >= 1 and "," in line:
            candidate = line.split(",")[0].split()[-1]
            if candidate.isalpha():
                author = candidate
                break

    meta = {"author": author, "year": year, "journal": None}
    print(f"  [heuristic] (low confidence) -> {meta}")
    return meta


# --------------------------------------------------------------------------
# Filename construction
# --------------------------------------------------------------------------

def sanitize(component: str) -> str:
    component = component.strip()
    component = re.sub(r'[\\/:"*?<>|]+', '', component)
    component = re.sub(r'\s+', '', component)
    component = re.sub(r'[^A-Za-z0-9]+', '', component)
    return component or "Unknown"


def build_filename(meta: dict) -> str:
    year = sanitize(meta["year"] or "UnknownYear")
    author = sanitize(meta["author"] or "UnknownAuthor")
    journal = sanitize(meta["journal"] or "UnknownJournal")
    return f"{year}_{author}_{journal}.pdf"


def unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 2
    while True:
        candidate = target.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


# --------------------------------------------------------------------------
# Main per-file pipeline
# --------------------------------------------------------------------------

def process_file(pdf_path: Path, dry_run: bool, copy: bool) -> None:
    print(f"\nProcessing: {pdf_path}")
    text = extract_first_pages_text(pdf_path)
    if not text.strip():
        print("  [skip] no extractable text (possibly a scanned PDF — try OCR first, e.g. `ocrmypdf`)")
        return

    meta = dict(EMPTY_META)
    title_cache = {}

    ordered_strategies = [
        ("doi", lambda: strategy_doi_crossref(text, pdf_path)),
        ("arxiv", lambda: strategy_arxiv(text, pdf_path)),
        ("pubmed", lambda: strategy_pubmed(text, pdf_path)),
        ("docinfo", lambda: strategy_docinfo(text, pdf_path)),
    ]

    for name, fn in ordered_strategies:
        if is_complete(meta):
            break
        result = fn()
        merge_missing(meta, result)
        time.sleep(0.2)  # be polite to free APIs

    # title-based strategies need the guessed title computed once
    if not is_complete(meta):
        if "title" not in title_cache:
            title_cache["title"] = extract_title_by_fontsize(pdf_path)
            if title_cache["title"]:
                print(f"  [title]    guessed: \"{title_cache['title']}\"")
            else:
                print("  [title]    could not confidently identify a title")

        for name, fn in [
            ("title_crossref", lambda: strategy_title_crossref(text, pdf_path, title_cache)),
            ("title_openalex", lambda: strategy_title_openalex(text, pdf_path, title_cache)),
        ]:
            if is_complete(meta):
                break
            result = fn()
            merge_missing(meta, result)
            time.sleep(0.2)

    if not is_complete(meta):
        merge_missing(meta, strategy_issn(text, pdf_path))
        time.sleep(0.2)

    if not is_complete(meta):
        merge_missing(meta, strategy_heuristic(text, pdf_path))

    print(f"  Final metadata: author={meta['author']!r}, year={meta['year']!r}, journal={meta['journal']!r}")
    if not is_complete(meta):
        print("  [note] one or more fields could not be determined — filename will contain 'Unknown*' for those")

    new_name = build_filename(meta)
    new_path = unique_path(pdf_path.with_name(new_name))

    if dry_run:
        print(f"  [dry-run] would rename to: {new_path.name}")
        return

    if copy:
        shutil.copy2(pdf_path, new_path)
        print(f"  Copied to: {new_path.name}")
    else:
        pdf_path.rename(new_path)
        print(f"  Renamed to: {new_path.name}")


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------

def is_pdf(path: Path) -> bool:
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def collect_pdfs(paths) -> list:
    files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and is_pdf(candidate):
                    files.append(candidate)
        elif path.is_file() and is_pdf(path):
            files.append(path)
        else:
            print(f"[warn] skipping unrecognized path: {p}")
    return files


def main():
    global API_EMAIL
    parser = argparse.ArgumentParser(description="Rename research paper PDFs to YEAR_Author_Journal.pdf")
    parser.add_argument("paths", nargs="+", help="PDF file(s) or folder(s) to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without renaming")
    parser.add_argument("--copy", action="store_true", help="Copy to new name instead of renaming in place")
    parser.add_argument("--email", default=API_EMAIL, help="Email for API polite-pool access (recommended)")
    args = parser.parse_args()
    API_EMAIL = args.email

    if not HAVE_PYPDF:
        print("[note] pypdf not installed — embedded-metadata strategy will be skipped.\n"
              "       Install with: pip install pypdf --break-system-packages\n")

    pdfs = collect_pdfs(args.paths)
    if not pdfs:
        print("No PDF files found.")
        return

    print(f"Found {len(pdfs)} PDF file(s).")
    for pdf in pdfs:
        process_file(pdf, dry_run=args.dry_run, copy=args.copy)


if __name__ == "__main__":
    main()
