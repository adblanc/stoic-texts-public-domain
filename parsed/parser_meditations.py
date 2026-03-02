#!/usr/bin/env python3
"""
Parser for Marcus Aurelius' Meditations (George Long translation).

Source: The Internet Classics Archive version of the Meditations.
This parser is specifically calibrated to the structural patterns in that file:
  - Header: 3 lines of Internet Classics Archive metadata, title, author, translator
  - 12 books delimited by "BOOK ONE" through "BOOK TWELVE"
  - Sections within books separated by blank lines (paragraph breaks)
  - Location colophons at end of Books 1 and 2 ("Among the Quadi...", "This in Carnuntum.")
  - Footer: "THE END", separator, copyright statement

Usage:
    python parser.py <path_to_txt_file>
    python parser.py <path_to_txt_file> --dry-run
"""

import argparse
import json
import logging
import random
import re
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Matches book headers like "BOOK ONE", "BOOK TWO", ..., "BOOK TWELVE"
# These appear on their own line within the text body.
RE_BOOK_HEADER = re.compile(
    r'^BOOK\s+(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE)$'
)

# Matches the horizontal rule separator used between books:
# A line consisting entirely of dashes (at least 10).
RE_SEPARATOR = re.compile(r'^-{10,}$')

# Matches "THE END" marker at the conclusion of the text.
RE_THE_END = re.compile(r'^THE END$')

# Matches the location colophon at the end of Book 1.
# "Among the Quadi at the Granua."
RE_COLOPHON_BOOK1 = re.compile(r'^Among the Quadi at the Granua\.\s*$')

# Matches the location colophon at the end of Book 2.
# "This in Carnuntum."
RE_COLOPHON_BOOK2 = re.compile(r'^This in Carnuntum\.\s*$')

# Matches the Internet Classics Archive header line.
RE_ICA_HEADER = re.compile(r'^Provided by The Internet Classics Archive')

# Matches the copyright section at the end of the file.
RE_COPYRIGHT = re.compile(r'^Copyright statement:')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map from English ordinal words to integers.
WORD_TO_NUM = {
    'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5, 'SIX': 6,
    'SEVEN': 7, 'EIGHT': 8, 'NINE': 9, 'TEN': 10, 'ELEVEN': 11, 'TWELVE': 12,
}

BOOK_WORD_TO_TITLE = {
    1: "Book One", 2: "Book Two", 3: "Book Three", 4: "Book Four",
    5: "Book Five", 6: "Book Six", 7: "Book Seven", 8: "Book Eight",
    9: "Book Nine", 10: "Book Ten", 11: "Book Eleven", 12: "Book Twelve",
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------


def read_file(path: str) -> str:
    """Read the file, trying UTF-8 first, falling back to latin-1."""
    p = Path(path)
    if not p.exists():
        log.error("File not found: %s", path)
        sys.exit(1)
    try:
        return p.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        log.warning("UTF-8 decode failed, falling back to latin-1")
        return p.read_text(encoding='latin-1')


# ---------------------------------------------------------------------------
# Stripping header and footer
# ---------------------------------------------------------------------------


def strip_header_footer(lines: list[str]) -> list[str]:
    """
    Remove the Internet Classics Archive header (lines before 'BOOK ONE')
    and the footer (from 'THE END' onwards including the copyright).

    Returns the lines of actual philosophical content.
    Logs what is removed.
    """
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Find the first book header — content starts here.
        if RE_BOOK_HEADER.match(stripped) and stripped == 'BOOK ONE':
            start_idx = i
            break

    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if RE_THE_END.match(stripped):
            end_idx = i
            break

    if start_idx is None:
        log.error("Could not find 'BOOK ONE' — unable to locate content start.")
        sys.exit(1)
    if end_idx is None:
        log.error("Could not find 'THE END' — unable to locate content end.")
        sys.exit(1)

    # Log removed header
    header_lines = lines[:start_idx]
    header_text = '\n'.join(l.strip() for l in header_lines if l.strip())
    log.info("Stripped header (%d lines):\n  %s", start_idx, header_text.replace('\n', '\n  '))

    # Log removed footer
    footer_lines = lines[end_idx:]
    footer_text = '\n'.join(l.strip() for l in footer_lines if l.strip())
    log.info("Stripped footer (%d lines):\n  %s", len(lines) - end_idx, footer_text.replace('\n', '\n  '))

    return lines[start_idx:end_idx]


# ---------------------------------------------------------------------------
# Parsing books
# ---------------------------------------------------------------------------


def split_into_books(lines: list[str]) -> list[tuple[int, list[str]]]:
    """
    Split the content lines into books.
    Returns a list of (book_number, book_lines) tuples.
    """
    books = []
    current_book_num = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        m = RE_BOOK_HEADER.match(stripped)
        if m:
            # Save previous book if any.
            if current_book_num is not None:
                books.append((current_book_num, current_lines))
            current_book_num = WORD_TO_NUM[m.group(1)]
            current_lines = []
            continue

        # Skip separator lines between books.
        if RE_SEPARATOR.match(stripped):
            continue

        current_lines.append(line)

    # Don't forget the last book.
    if current_book_num is not None:
        books.append((current_book_num, current_lines))

    return books


# ---------------------------------------------------------------------------
# Parsing sections within a book
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """
    Clean a section's text:
    - Normalize line endings.
    - Collapse runs of 3+ newlines into 2 (preserving paragraph breaks).
    - Collapse multiple spaces into one.
    - Strip leading/trailing whitespace.
    """
    # Normalize line endings to \n.
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Collapse multiple blank lines into a single paragraph break.
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple spaces into one (but not newlines).
    text = re.sub(r'[^\S\n]+', ' ', text)
    # Strip leading/trailing whitespace from each line.
    text = '\n'.join(l.strip() for l in text.split('\n'))
    # Strip overall.
    text = text.strip()
    return text


def split_book_into_sections(book_num: int, lines: list[str]) -> list[str]:
    """
    Split a book's lines into individual sections (meditations).

    Sections are separated by one or more blank lines.

    Special handling:
    - Book 1: the colophon "Among the Quadi at the Granua." is appended
      to the last section.
    - Book 2: the colophon "This in Carnuntum." is appended to the last
      section.
    """
    sections: list[str] = []
    current_paragraph_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == '':
            # Blank line — if we have accumulated text, that's a section boundary.
            if current_paragraph_lines:
                sections.append('\n'.join(current_paragraph_lines))
                current_paragraph_lines = []
        else:
            current_paragraph_lines.append(stripped)

    # Flush remaining.
    if current_paragraph_lines:
        sections.append('\n'.join(current_paragraph_lines))

    # Handle colophons: attach to last section rather than making standalone.
    if book_num == 1 and sections:
        last = sections[-1]
        if RE_COLOPHON_BOOK1.match(last.strip()):
            # The colophon is its own "section" — merge it into the previous.
            colophon = sections.pop()
            if sections:
                sections[-1] = sections[-1] + '\n\n' + colophon
                log.info("Book 1: attached colophon '%s' to last section.", colophon.strip())
            else:
                sections.append(colophon)

    if book_num == 2 and sections:
        last = sections[-1]
        if RE_COLOPHON_BOOK2.match(last.strip()):
            colophon = sections.pop()
            if sections:
                sections[-1] = sections[-1] + '\n\n' + colophon
                log.info("Book 2: attached colophon '%s' to last section.", colophon.strip())
            else:
                sections.append(colophon)

    return sections


# ---------------------------------------------------------------------------
# Building the JSON structure
# ---------------------------------------------------------------------------


def build_sections(books: list[tuple[int, list[str]]]) -> list[dict]:
    """
    Build the list of section dictionaries from parsed books.
    """
    all_sections = []

    for book_num, book_lines in books:
        raw_sections = split_book_into_sections(book_num, book_lines)
        for sec_idx, raw_text in enumerate(raw_sections, start=1):
            text = clean_text(raw_text)
            if not text:
                log.warning("Book %d, Section %d: empty after cleaning — skipping.", book_num, sec_idx)
                continue

            word_count = len(text.split())
            section_id = f"meditations_b{book_num}_s{sec_idx}"
            source_ref = f"Book {book_num}, Section {sec_idx}"

            section = {
                "id": section_id,
                "book": book_num,
                "book_title": BOOK_WORD_TO_TITLE.get(book_num),
                "chapter": None,
                "chapter_title": None,
                "section": sec_idx,
                "letter_number": None,
                "text": text,
                "word_count": word_count,
                "source_reference": source_ref,
            }
            all_sections.append(section)

    return all_sections


def build_metadata() -> dict:
    """Build the metadata block."""
    return {
        "work": "The Meditations",
        "author": "Marcus Aurelius",
        "translator": "George Long",
        "source": "The Internet Classics Archive",
        "parsed_date": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(sections: list[dict], original_lines: list[str]) -> None:
    """
    Run validation checks and report results.
    """
    total_sections = len(sections)
    total_words = sum(s['word_count'] for s in sections)

    print("\n=== Validation Report ===")
    print(f"Total sections parsed: {total_sections}")
    print(f"Total word count: {total_words}")

    # Sections with unusually short text (under 20 words).
    short = [s for s in sections if s['word_count'] < 20]
    if short:
        print(f"\nSections with fewer than 20 words ({len(short)}):")
        for s in short:
            print(f"  {s['source_reference']}: {s['word_count']} words — \"{s['text'][:80]}...\"")
    else:
        print("\nNo sections with fewer than 20 words.")

    # Check for structural markers in the original file that were NOT parsed.
    # We look for anything that looks like a book header but wasn't matched.
    unparsed_markers = []
    for i, line in enumerate(original_lines):
        stripped = line.strip()
        # Check for BOOK followed by something unexpected.
        if stripped.startswith('BOOK ') and not RE_BOOK_HEADER.match(stripped):
            unparsed_markers.append((i + 1, stripped))

    if unparsed_markers:
        print(f"\nUnparsed structural markers ({len(unparsed_markers)}):")
        for lineno, text in unparsed_markers:
            print(f"  Line {lineno}: \"{text}\"")
    else:
        print("\nNo unparsed structural markers found.")

    # Books found — verify we have all 12.
    book_nums = sorted(set(s['book'] for s in sections))
    expected = list(range(1, 13))
    if book_nums != expected:
        print(f"\nWARNING: Expected books 1-12, found: {book_nums}")
    else:
        print("\nAll 12 books found.")

    # Sections per book.
    print("\nSections per book:")
    for b in expected:
        count = sum(1 for s in sections if s['book'] == b)
        print(f"  Book {b:>2}: {count} sections")


def print_sample(sections: list[dict]) -> None:
    """Print 3 randomly selected sections for manual verification."""
    print("\n=== Sample Sections (3 random) ===")
    sample_size = min(3, len(sections))
    samples = random.sample(sections, sample_size)
    for s in samples:
        print(f"\n--- {s['source_reference']} (id={s['id']}, words={s['word_count']}) ---")
        # Print first 500 chars to keep output manageable.
        preview = s['text'][:500]
        if len(s['text']) > 500:
            preview += '...'
        print(preview)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description='Parse Marcus Aurelius Meditations (George Long / Internet Classics Archive) into JSON.'
    )
    parser.add_argument('file', help='Path to the meditations.txt file')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print structural analysis without writing the output file.',
    )
    args = parser.parse_args()

    # Read file.
    raw = read_file(args.file)
    all_lines = raw.splitlines()
    log.info("Read %d lines from %s", len(all_lines), args.file)

    # Strip header and footer.
    content_lines = strip_header_footer(all_lines)
    log.info("Content lines after stripping: %d", len(content_lines))

    # Split into books.
    books = split_into_books(content_lines)
    log.info("Found %d books.", len(books))

    if args.dry_run:
        print("\n=== Dry Run: Structural Analysis ===")
        for book_num, book_lines in books:
            sections = split_book_into_sections(book_num, book_lines)
            print(f"Book {book_num}: {len(sections)} sections, {len(book_lines)} lines")
            for i, sec in enumerate(sections, 1):
                text = clean_text(sec)
                wc = len(text.split())
                preview = text[:80].replace('\n', ' ')
                print(f"  Section {i}: {wc} words — \"{preview}...\"")
        return

    # Build sections.
    sections = build_sections(books)

    # Build output.
    output = {
        "metadata": build_metadata(),
        "sections": sections,
    }

    # Validate.
    validate(sections, all_lines)

    # Print sample.
    print_sample(sections)

    # Write output.
    out_path = Path('meditations_long.json')
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nOutput written to: {out_path.resolve()}")
    print(f"Sections: {len(sections)}, Total words: {sum(s['word_count'] for s in sections)}")


if __name__ == '__main__':
    main()
