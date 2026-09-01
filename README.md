# LSPO
Literature Survey Pdf Organiser

A command-line tool for Linux that automatically renames research-paper PDFs to a consistent `YEAR_Author_Journal.pdf` format, using metadata pulled from the PDF itself and from free scholarly APIs.

---

## What it does

Given one or more PDF files (or folders of them), the tool:

1. Extracts text and layout information from the first few pages.
2. Tries up to eight independent strategies — most reliable first — to determine the **publication year**, **first author's surname**, and **journal name**.
3. Merges partial results from multiple strategies if no single one finds everything.
4. Renames (or copies) the file to `YEAR_Author_Journal.pdf`, sanitizing the name and avoiding overwrites.

---

## Requirements

- Python 3.9+
- Linux (tested), should also work on macOS

Install dependencies:

```bash
pip install pdfplumber requests pypdf --break-system-packages
```

| Package | Purpose | Required? |
|---|---|---|
| `pdfplumber` | Extract text and character-level layout from PDFs | Yes |
| `requests` | Query Crossref / arXiv / PubMed / OpenAlex APIs | Yes |
| `pypdf` | Read embedded PDF document metadata (Author, CreationDate) | Optional — one fewer strategy is used if missing |

---

## Installation

Put the script somewhere permanent and make it runnable from any directory.

```bash
mkdir -p ~/bin
mv rename_paper.py ~/bin/rename_paper
chmod +x ~/bin/rename_paper
```

Make sure `~/bin` is on your `PATH`:

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

You should now be able to run `rename-paper` from anywhere. (Alternatively, define a shell alias — `alias rename-paper='python3 ~/bin/rename_paper.py'` — if you'd rather not add to `PATH`.)

---

## Usage

```bash
# Single file
rename-paper paper.pdf

# Multiple files
rename-paper paper1.pdf paper2.pdf

# Whole folder, recursive
rename-paper ~/papers/

# Preview only — no files are changed
rename-paper --dry-run ~/papers/

# Copy to the new name instead of renaming in place
rename-paper --copy ~/papers/

# Recommended: identify yourself to the APIs for faster, more reliable lookups
rename-paper --email you@example.com ~/papers/
```

### Options

| Flag | Description |
|---|---|
| `paths` | One or more PDF files and/or folders (folders are searched recursively) |
| `--dry-run` | Show what the new filename would be, without renaming anything |
| `--copy` | Copy the file to the new name instead of renaming the original |
| `--email ADDRESS` | Email used in API requests (Crossref/OpenAlex "polite pool" — improves reliability and speed) |

### File detection

Files are recognized as PDFs either by their `.pdf` extension **or** by their binary signature (`%PDF-` header). This means files downloaded without an extension — a common issue with some journal repository download links — are still detected correctly.

---

## How metadata is found

The tool runs through the following strategies in order, stopping as soon as author, year, and journal are all known. If no single strategy finds everything, results are **merged**: a later strategy only fills in fields still missing from earlier ones.

| Order | Strategy | Trigger | Fills in |
|---|---|---|---|
| 1 | **DOI → Crossref** | A DOI (`10.xxxx/...`) found in the extracted text | author, year, journal |
| 2 | **arXiv ID → arXiv API** | An `arXiv:XXXX.XXXXX` identifier in the text or filename | author, year, journal (or "arXiv preprint" if unpublished) |
| 3 | **PMID → PubMed** | A `PMID: XXXXXXXX` reference in the text | author, year, journal |
| 4 | **Embedded PDF metadata** | The PDF's internal Author / CreationDate fields (via `pypdf`) | author, year |
| 5 | **Title → Crossref search** | Largest-font text block near the top of page 1, matched bibliographically | author, year, journal |
| 6 | **Title → OpenAlex search** | Same guessed title, searched against a different index (broader preprint/journal coverage) | author, year, journal |
| 7 | **ISSN → Crossref journal lookup** | An ISSN (`XXXX-XXXX`) found in the text | journal only |
| 8 | **Text heuristics** | Crude pattern-matching on the first page (last resort, low confidence) | author, year |

The title is guessed by finding the largest cluster of large-font text near the top of the first page — this is usually the paper's title regardless of publisher layout. Title-based API matches are only accepted if they're a close text match (similarity ≥ 0.55) to the extracted title, to avoid confidently attaching the wrong paper's metadata.

Any field that remains unknown after all strategies run is filled with a placeholder: `UnknownAuthor`, `UnknownYear`, or `UnknownJournal`.

---

## Example output

```
$ rename-paper --dry-run --email you@example.com paper.pdf

Found 1 PDF file(s).

Processing: paper.pdf
  [doi]      found DOI: 10.1038/s41586-021-03819-2
  [doi]      -> {'author': 'Smith', 'year': 2021, 'journal': 'Nature'}
  Final metadata: author='Smith', year=2021, journal='Nature'
  [dry-run] would rename to: 2021_Smith_Nature.pdf
```

If the DOI path fails, you'll see the tool fall through to later strategies, e.g.:

```
  [doi]      found DOI: 10.1234/example.doi.2021
  [doi]      Crossref returned 404
  [title]    guessed: "Deep Learning Approaches for Climate Prediction Models"
  [title/crossref] matched (similarity 0.82): "Deep Learning Approaches for Climate Prediction Models"
  [title/crossref] -> {'author': 'Doe', 'year': 2021, 'journal': 'Journal of Example Studies'}
```

---

## Limitations

- **Scanned / image-only PDFs** have no extractable text and will be skipped. Run them through OCR first, e.g.:
  ```bash
  pip install ocrmypdf --break-system-packages   # or: sudo apt install ocrmypdf
  ocrmypdf input.pdf output.pdf
  ```
- **Very unusual layouts** (multi-column title pages, non-standard fonts, heavily stylized cover pages) can confuse the font-size title guess.
- **Network dependent**: strategies 1, 2, 3, 5, 6, and 7 require internet access to Crossref, arXiv, PubMed, and OpenAlex. Without connectivity, only strategies 4 and 8 are available, which are the least reliable.
- **Rate limits**: free APIs may throttle heavy batch use. Passing `--email` puts you in each service's "polite pool," which is faster and less likely to be rate-limited. For very large batches, consider adding delays between runs if you hit errors.
- **Ambiguous or low-quality matches**: title-based search requires a similarity score of at least 0.55 against the extracted title before accepting a match, to reduce the risk of misidentifying a paper. Very short or generic titles are more likely to be rejected or to match the wrong paper.

---

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| `No PDF files found` | Check the path is correct; the file may not actually be a PDF (verify with `file yourfile`) |
| Every strategy fails, ends up `UnknownX` | Likely a scanned PDF with no text layer — try OCR, or check with `--dry-run` what text was extracted |
| Crossref/OpenAlex returns errors repeatedly | Add `--email you@example.com`; check your internet connection; the API may be temporarily rate-limiting you |
| `pypdf` strategy skipped | Install it: `pip install pypdf --break-system-packages` |
| Wrong author/journal attached | The title-similarity match may have been a false positive on a generic title — verify with `--dry-run` before trusting bulk renames on ambiguous papers |

---

## Safety notes

- Always run with `--dry-run` first on a new batch of files to confirm the results look correct before committing to a rename.
- Use `--copy` instead of an in-place rename if you want to keep the originals untouched while testing.
