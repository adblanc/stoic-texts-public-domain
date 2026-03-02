#!/usr/bin/env python3
"""
Parser for Epictetus' Discourses (P.E. Matheson translation).

Source: sacred-texts.com transcription of Matheson's 1916 translation, as
reprinted in Whitney J. Oates (ed.), The Stoic and Epicurean Philosophers
(Random House, 1940).

This parser is specifically calibrated to that file's structural patterns:
  - 4 Books (BOOK I–IV), each containing numbered chapters (CHAPTER I–XXX etc.)
  - Chapter titles on a separate line following the CHAPTER heading
  - Arrian's Preface before Book I
  - Fragments section (36 numbered fragments + 10a) after Book IV
  - The Manual (Enchiridion) with 53 numbered sections after Fragments
  - Notes sections after each book (^N-N format) — excluded
  - Page markers [p. NNN] throughout — excluded
  - sacred-texts.com header lines repeated throughout — excluded
  - Subject Index at end — excluded
  - Footnote reference markers [*X-N] inline — removed

Usage:
    python parser_discourses.py <path_to_txt_file>
    python parser_discourses.py <path_to_txt_file> --dry-run
"""

import argparse
import json
import logging
import random
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Matches a BOOK header: "BOOK I", "BOOK II", "BOOK III", "BOOK IV"
RE_BOOK_HEADER = re.compile(r'^BOOK ([IVX]+)$')

# Matches a CHAPTER header: "CHAPTER I", "CHAPTER XXX", etc.
RE_CHAPTER_HEADER = re.compile(r'^CHAPTER ([IVXLC]+)$')

# Matches page markers like "[p. 225]"
RE_PAGE_MARKER = re.compile(r'^\[p\.\s*\d+\]$')

# Matches the sacred-texts.com header line repeated throughout the file.
# "The Discourses of Epictetus, tr. by P.E Matheson, [1916], at sacred-texts.com"
RE_SACRED_TEXTS_HEADER = re.compile(
    r'^The Discourses of Epictetus, tr\. by P\.E\.? Matheson'
)

# Matches note lines: "^1-1 ...", "^2-3 ...", "^f-1 ...", "^m-1 ..." etc.
RE_NOTE_LINE = re.compile(r'^\^[a-z0-9]+-\d+')

# Matches the "Book N Notes" or "Book N. Notes." section headers.
RE_NOTES_HEADER = re.compile(r'^Book [IVX]+\.?\s*Notes\.?$')

# Matches "The Discourses." separator line.
RE_DISCOURSES_SEPARATOR = re.compile(r'^The Discourses\.$')

# Matches the PREFACE header.
RE_PREFACE = re.compile(r'^PREFACE$')

# Matches "ARRIANUS TO LUCIUS GELLIUS GREETING"
RE_PREFACE_SUBTITLE = re.compile(r'^ARRIANUS TO LUCIUS GELLIUS GREETING$')

# Matches the FRAGMENTS section header.
RE_FRAGMENTS_HEADER = re.compile(r'^FRAGMENTS\s*(\[\*[a-z0-9\-]+\])?\s*$')

# Matches a fragment number on its own line: "1", "2", ..., "10a", "36"
# Also matches Roman numeral "I" for Fragment 1 which uses Roman numeral format.
# May have trailing footnote markers like [*f-3].
RE_FRAGMENT_NUMBER = re.compile(r'^(\d+[a-z]?)\s*(?:\[\*[a-z0-9\-]+\])?\s*$')

# Fragment 1 is special: uses Roman numeral "I" instead of Arabic "1".
RE_FRAGMENT_1_ROMAN = re.compile(r'^I$')

# Matches a fragment title line (all caps, following a fragment number).
# E.g., "FROM ARRIAN THE PUPIL OF EPICTETUS. TO ONE DISCOURSING ON SUBSTANCE"
RE_FRAGMENT_TITLE = re.compile(r'^(FROM |RUFUS:)')

# Matches the Manual/Enchiridion header.
RE_MANUAL_HEADER = re.compile(
    r'^THE MANUAL \[ENCHIRIDION\] OF EPICTETUS\s*(\[\*[a-z0-9\-]+\])?\s*$'
)

# Matches a manual section number on its own line: "1", "2", ..., "53"
# May have trailing footnote markers like [*m-3].
RE_MANUAL_SECTION_NUMBER = re.compile(r'^(\d+)\s*(?:\[\*[a-z0-9\-]+\])?\s*$')

# Matches the Subject Index header.
RE_INDEX_HEADER = re.compile(r'^SUBJECT INDEX TO THE DISCOURSES')

# Matches inline footnote reference markers like [*1-1], [*f-2], [*m-1].
RE_FOOTNOTE_REF = re.compile(r'\s*\[\*[a-z0-9\-]+\]')

# Matches inline page markers [p. NNN] that appear mid-paragraph.
RE_INLINE_PAGE = re.compile(r'\[p\.\s*\d+\]')

# ---------------------------------------------------------------------------
# Roman numeral conversion
# ---------------------------------------------------------------------------

_ROMAN_VALUES = [
    ('M', 1000), ('CM', 900), ('D', 500), ('CD', 400),
    ('C', 100), ('XC', 90), ('L', 50), ('XL', 40),
    ('X', 10), ('IX', 9), ('V', 5), ('IV', 4), ('I', 1),
]


def roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to an integer."""
    result = 0
    i = 0
    for numeral, value in _ROMAN_VALUES:
        while s[i:i + len(numeral)] == numeral:
            result += value
            i += len(numeral)
    return result


def int_to_roman(n: int) -> str:
    """Convert an integer to a Roman numeral string."""
    result = []
    for numeral, value in _ROMAN_VALUES:
        while n >= value:
            result.append(numeral)
            n -= value
    return ''.join(result)


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
# Line classification helpers
# ---------------------------------------------------------------------------


def is_noise_line(line: str) -> bool:
    """
    Return True if the line is structural noise that should be skipped:
    - Page markers: [p. 225]
    - sacred-texts.com headers
    - Whitespace-only lines that are just "  " (indented blanks used as spacers)
    """
    stripped = line.strip()
    if RE_PAGE_MARKER.match(stripped):
        return True
    if RE_SACRED_TEXTS_HEADER.match(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """
    Clean section text:
    - Remove inline footnote markers [*1-1] etc.
    - Remove inline page markers [p. NNN]
    - Normalize whitespace
    - Preserve paragraph breaks as single newlines
    """
    # Remove footnote reference markers.
    text = RE_FOOTNOTE_REF.sub('', text)
    # Remove inline page markers.
    text = RE_INLINE_PAGE.sub('', text)
    # Normalize line endings.
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Collapse multiple blank lines into a single paragraph break.
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple spaces into one (but not newlines).
    text = re.sub(r'[^\S\n]+', ' ', text)
    # Strip leading/trailing whitespace from each line.
    text = '\n'.join(l.strip() for l in text.split('\n'))
    # Remove empty lines at start/end.
    text = text.strip()
    return text


# ---------------------------------------------------------------------------
# Parsing: Discourses (Books I–IV with chapters)
# ---------------------------------------------------------------------------


def parse_discourses(lines: list[str]) -> tuple:
    """
    Parse the Discourses section (Books I–IV).

    Returns:
        (preface_text, chapters_list)
        where chapters_list is a list of dicts with keys:
          book_num, chapter_num, chapter_title, lines
    """
    # Phase 1: find the boundaries of the main content.
    # Content starts at "PREFACE" and ends at "Book IV. Notes."
    preface_start = None
    book1_start = None
    content_end = None  # Line index of the last chapter's last content line.

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if RE_PREFACE.match(stripped) and preface_start is None:
            preface_start = i
        if stripped == 'BOOK I' and book1_start is None:
            book1_start = i
        # Notes sections signal end of a book's chapters — but we want the
        # end of Book IV's chapters, which is followed by "Book IV. Notes."
        if RE_NOTES_HEADER.match(stripped) and 'IV' in stripped:
            content_end = i
            break
        # Also handle "The Discourses." + "Book IV. Notes." pattern:
        if RE_DISCOURSES_SEPARATOR.match(stripped):
            # Check if next non-blank line is "Book IV. Notes."
            for j in range(i + 1, min(i + 5, len(lines))):
                if RE_NOTES_HEADER.match(lines[j].strip()) and 'IV' in lines[j]:
                    content_end = i
                    break
            if content_end is not None:
                break
        i += 1

    if preface_start is None:
        log.error("Could not find PREFACE.")
        sys.exit(1)
    if book1_start is None:
        log.error("Could not find BOOK I.")
        sys.exit(1)
    if content_end is None:
        log.warning("Could not find Book IV Notes marker; using end of file.")
        content_end = len(lines)

    log.info("Preface starts at line %d", preface_start + 1)
    log.info("Book I starts at line %d", book1_start + 1)
    log.info("Discourses content ends at line %d", content_end)

    # Phase 2: extract preface text.
    preface_lines = []
    for idx in range(preface_start, book1_start):
        stripped = lines[idx].strip()
        if RE_PREFACE.match(stripped):
            continue
        if RE_PREFACE_SUBTITLE.match(stripped):
            continue
        if is_noise_line(lines[idx]):
            continue
        preface_lines.append(stripped)
    preface_text = clean_text('\n'.join(preface_lines))

    # Phase 3: parse books and chapters.
    chapters = []
    current_book = None
    current_chapter_num = None
    current_chapter_title = None
    current_chapter_lines = []
    awaiting_title = False  # True after seeing CHAPTER X, before seeing the title
    in_notes = False  # True when we enter a notes section

    for idx in range(book1_start, content_end):
        stripped = lines[idx].strip()

        # Skip noise lines.
        if is_noise_line(lines[idx]):
            continue

        # Check for notes section (to skip).
        if RE_NOTES_HEADER.match(stripped) or RE_DISCOURSES_SEPARATOR.match(stripped):
            in_notes = True
            continue
        if RE_NOTE_LINE.match(stripped):
            in_notes = True
            continue

        # Check for BOOK header (ends notes section if we were in one).
        bm = RE_BOOK_HEADER.match(stripped)
        if bm:
            in_notes = False
            # Save previous chapter if any.
            if current_chapter_num is not None and current_book is not None:
                chapters.append({
                    'book_num': current_book,
                    'chapter_num': current_chapter_num,
                    'chapter_title': current_chapter_title,
                    'lines': current_chapter_lines,
                })
            current_book = roman_to_int(bm.group(1))
            current_chapter_num = None
            current_chapter_title = None
            current_chapter_lines = []
            awaiting_title = False
            continue

        if in_notes:
            continue

        # Check for CHAPTER header.
        cm = RE_CHAPTER_HEADER.match(stripped)
        if cm:
            # Save previous chapter.
            if current_chapter_num is not None and current_book is not None:
                chapters.append({
                    'book_num': current_book,
                    'chapter_num': current_chapter_num,
                    'chapter_title': current_chapter_title,
                    'lines': current_chapter_lines,
                })
            current_chapter_num = roman_to_int(cm.group(1))
            current_chapter_title = None
            current_chapter_lines = []
            awaiting_title = True
            continue

        # If awaiting title, the next non-blank line is the chapter title.
        if awaiting_title:
            if stripped:
                current_chapter_title = stripped
                awaiting_title = False
            continue

        # Accumulate content lines.
        if current_chapter_num is not None:
            current_chapter_lines.append(stripped)

    # Flush last chapter.
    if current_chapter_num is not None and current_book is not None:
        chapters.append({
            'book_num': current_book,
            'chapter_num': current_chapter_num,
            'chapter_title': current_chapter_title,
            'lines': current_chapter_lines,
        })

    return preface_text, chapters


# ---------------------------------------------------------------------------
# Parsing: Fragments
# ---------------------------------------------------------------------------


def parse_fragments(lines: list[str]) -> list[dict]:
    """
    Parse the Fragments section.
    Returns a list of dicts with keys: number (str), title, lines.
    """
    # Find the FRAGMENTS header.
    frag_start = None
    frag_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if RE_FRAGMENTS_HEADER.match(stripped):
            frag_start = i + 1
        # Fragment notes start with ^f-
        if frag_start is not None and RE_NOTE_LINE.match(stripped) and stripped.startswith('^f-'):
            frag_end = i
            break
        # Manual header also ends fragments.
        if frag_start is not None and RE_MANUAL_HEADER.match(stripped):
            frag_end = i
            break

    if frag_start is None:
        log.warning("Could not find FRAGMENTS section.")
        return []

    if frag_end is None:
        frag_end = len(lines)

    log.info("Fragments section: lines %d–%d", frag_start + 1, frag_end)

    fragments = []
    current_num = None
    current_title = None
    current_lines = []
    awaiting_title = False

    for idx in range(frag_start, frag_end):
        stripped = lines[idx].strip()

        if is_noise_line(lines[idx]):
            continue

        # Check for fragment number.
        fm = RE_FRAGMENT_NUMBER.match(stripped)
        # Fragment 1 uses Roman numeral "I" instead of Arabic "1".
        is_frag1 = (current_num is None and RE_FRAGMENT_1_ROMAN.match(stripped))
        if fm or is_frag1:
            # Save previous fragment.
            if current_num is not None:
                fragments.append({
                    'number': current_num,
                    'title': current_title,
                    'lines': current_lines,
                })
            current_num = fm.group(1) if fm else '1'
            current_title = None
            current_lines = []
            awaiting_title = True
            continue

        # Check for title line (FROM ..., RUFUS: ..., or all-caps title).
        if awaiting_title and stripped:
            if RE_FRAGMENT_TITLE.match(stripped) or stripped.isupper():
                current_title = stripped
                awaiting_title = False
                continue
            else:
                # No title; this is content.
                awaiting_title = False

        if current_num is not None:
            current_lines.append(stripped)

    # Flush last.
    if current_num is not None:
        fragments.append({
            'number': current_num,
            'title': current_title,
            'lines': current_lines,
        })

    return fragments


# ---------------------------------------------------------------------------
# Parsing: The Manual (Enchiridion)
# ---------------------------------------------------------------------------


def parse_manual(lines: list[str]) -> list[dict]:
    """
    Parse the Manual (Enchiridion) section.
    Returns a list of dicts with keys: number (int), lines.
    """
    # Find the Manual header.
    manual_start = None
    manual_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if RE_MANUAL_HEADER.match(stripped):
            manual_start = i + 1
        # Notes at end of manual start with ^m-
        if manual_start is not None and RE_NOTE_LINE.match(stripped) and stripped.startswith('^m-'):
            manual_end = i
            break
        # Index also ends the manual.
        if manual_start is not None and RE_INDEX_HEADER.match(stripped):
            manual_end = i
            break

    if manual_start is None:
        log.warning("Could not find THE MANUAL section.")
        return []

    if manual_end is None:
        manual_end = len(lines)

    log.info("Manual section: lines %d–%d", manual_start + 1, manual_end)

    sections = []
    current_num = None
    current_lines = []

    for idx in range(manual_start, manual_end):
        stripped = lines[idx].strip()

        if is_noise_line(lines[idx]):
            continue

        # Check for section number.
        nm = RE_MANUAL_SECTION_NUMBER.match(stripped)
        if nm:
            num = int(nm.group(1))
            # Sanity check: manual has sections 1–53.
            if 1 <= num <= 53:
                # Accept if: first section, or number is greater than current
                # (allows gaps but prevents matching random numbers in text).
                if current_num is None or num > current_num:
                    # Save previous section.
                    if current_num is not None:
                        sections.append({
                            'number': current_num,
                            'lines': current_lines,
                        })
                    current_num = num
                    current_lines = []
                    continue

        if current_num is not None:
            current_lines.append(stripped)

    # Flush last.
    if current_num is not None:
        sections.append({
            'number': current_num,
            'lines': current_lines,
        })

    return sections


# ---------------------------------------------------------------------------
# Building the JSON structure
# ---------------------------------------------------------------------------


BOOK_TITLES = {
    1: "Book I", 2: "Book II", 3: "Book III", 4: "Book IV",
}


def build_sections(
    preface_text: str,
    chapters: list[dict],
    fragments: list[dict],
    manual_sections: list[dict],
) -> list[dict]:
    """Build the list of section dictionaries."""
    all_sections = []

    # Preface.
    if preface_text:
        wc = len(preface_text.split())
        all_sections.append({
            "id": "discourses_preface",
            "book": None,
            "book_title": "Preface",
            "chapter": None,
            "chapter_title": "Arrianus to Lucius Gellius Greeting",
            "letter_number": None,
            "text": preface_text,
            "word_count": wc,
            "source_reference": "Preface",
        })

    # Discourse chapters.
    for ch in chapters:
        text = clean_text('\n'.join(ch['lines']))
        if not text:
            log.warning("Book %d, Chapter %d: empty — skipping.", ch['book_num'], ch['chapter_num'])
            continue
        wc = len(text.split())
        bk = ch['book_num']
        cn = ch['chapter_num']
        section_id = f"discourses_b{bk}_c{cn}"
        source_ref = f"Book {int_to_roman(bk)}, Chapter {int_to_roman(cn)}"

        all_sections.append({
            "id": section_id,
            "book": bk,
            "book_title": BOOK_TITLES.get(bk),
            "chapter": cn,
            "chapter_title": ch['chapter_title'],
            "letter_number": None,
            "text": text,
            "word_count": wc,
            "source_reference": source_ref,
        })

    # Fragments.
    for frag in fragments:
        text = clean_text('\n'.join(frag['lines']))
        if not text:
            log.warning("Fragment %s: empty — skipping.", frag['number'])
            continue
        wc = len(text.split())
        section_id = f"discourses_frag_{frag['number']}"
        title = frag.get('title')
        source_ref = f"Fragment {frag['number']}"

        all_sections.append({
            "id": section_id,
            "book": None,
            "book_title": "Fragments",
            "chapter": None,
            "chapter_title": title,
            "letter_number": None,
            "text": text,
            "word_count": wc,
            "source_reference": source_ref,
        })

    # Manual (Enchiridion).
    for sec in manual_sections:
        text = clean_text('\n'.join(sec['lines']))
        if not text:
            log.warning("Manual Section %d: empty — skipping.", sec['number'])
            continue
        wc = len(text.split())
        section_id = f"enchiridion_s{sec['number']}"
        source_ref = f"Enchiridion, Section {sec['number']}"

        all_sections.append({
            "id": section_id,
            "book": None,
            "book_title": "The Manual (Enchiridion)",
            "chapter": sec['number'],
            "chapter_title": None,
            "letter_number": None,
            "text": text,
            "word_count": wc,
            "source_reference": source_ref,
        })

    return all_sections


def build_metadata() -> dict:
    """Build the metadata block."""
    return {
        "work": "The Discourses of Epictetus, with the Enchiridion and Fragments",
        "author": "Epictetus (recorded by Arrian)",
        "translator": "P.E. Matheson (1916)",
        "source": "sacred-texts.com",
        "parsed_date": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(sections: list[dict], original_lines: list[str]) -> None:
    """Run validation checks and report results."""
    total = len(sections)
    total_words = sum(s['word_count'] for s in sections)

    print("\n=== Validation Report ===")
    print(f"Total sections parsed: {total}")
    print(f"Total word count: {total_words}")

    # Breakdown by type.
    preface = [s for s in sections if s['id'].startswith('discourses_preface')]
    discourse_chs = [s for s in sections if s['id'].startswith('discourses_b')]
    frags = [s for s in sections if s['id'].startswith('discourses_frag')]
    manual = [s for s in sections if s['id'].startswith('enchiridion')]

    print(f"\n  Preface: {len(preface)}")
    print(f"  Discourse chapters: {len(discourse_chs)}")
    print(f"  Fragments: {len(frags)}")
    print(f"  Enchiridion sections: {len(manual)}")

    # Chapters per book.
    print("\nDiscourse chapters per book:")
    for b in range(1, 5):
        count = sum(1 for s in discourse_chs if s['book'] == b)
        print(f"  Book {int_to_roman(b)}: {count} chapters")

    # Expected: Book I=30, II=26, III=26, IV=13 = 95 total.
    expected_total_chs = 95
    if len(discourse_chs) != expected_total_chs:
        print(f"  WARNING: Expected {expected_total_chs} chapters, found {len(discourse_chs)}")
    else:
        print(f"  All {expected_total_chs} chapters found.")

    # Short sections.
    short = [s for s in sections if s['word_count'] < 20]
    if short:
        print(f"\nSections with fewer than 20 words ({len(short)}):")
        for s in short:
            print(f"  {s['source_reference']}: {s['word_count']} words — \"{s['text'][:80]}...\"")
    else:
        print("\nNo sections with fewer than 20 words.")

    # Check for unparsed structural markers.
    unparsed = []
    for i, line in enumerate(original_lines):
        stripped = line.strip()
        m = RE_CHAPTER_HEADER.match(stripped)
        if m:
            num = roman_to_int(m.group(1))
            found = any(
                s['chapter'] == num and s['id'].startswith('discourses_b')
                for s in sections
            )
            if not found:
                unparsed.append((i + 1, stripped))
    if unparsed:
        print(f"\nUnparsed chapter markers ({len(unparsed)}):")
        for lineno, text in unparsed:
            print(f"  Line {lineno}: \"{text}\"")
    else:
        print("\nNo unparsed chapter markers.")

    # Word count stats.
    wcs = [s['word_count'] for s in sections]
    print(f"\nWord count range: {min(wcs)}–{max(wcs)}")
    print(f"Average words per section: {sum(wcs) // len(wcs)}")


def print_sample(sections: list[dict]) -> None:
    """Print 3 randomly selected sections for manual verification."""
    print("\n=== Sample Sections (3 random) ===")
    samples = random.sample(sections, min(3, len(sections)))
    for s in samples:
        title = s.get('chapter_title') or ''
        print(f"\n--- {s['source_reference']} — {title} "
              f"(id={s['id']}, words={s['word_count']}) ---")
        preview = s['text'][:500]
        if len(s['text']) > 500:
            preview += '...'
        print(preview)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Parse Epictetus' Discourses (Matheson / sacred-texts.com) into JSON."
    )
    parser.add_argument('file', help='Path to the discourses.txt file')
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

    # Parse Discourses (Books I–IV).
    preface_text, chapters = parse_discourses(all_lines)
    log.info("Parsed preface + %d chapters.", len(chapters))

    # Parse Fragments.
    fragments = parse_fragments(all_lines)
    log.info("Parsed %d fragments.", len(fragments))

    # Parse Manual (Enchiridion).
    manual_sections = parse_manual(all_lines)
    log.info("Parsed %d manual sections.", len(manual_sections))

    if args.dry_run:
        print("\n=== Dry Run: Structural Analysis ===")
        print(f"\nPreface: {len(preface_text.split())} words")
        print("\nDiscourse chapters:")
        for ch in chapters:
            text = clean_text('\n'.join(ch['lines']))
            wc = len(text.split()) if text else 0
            print(f"  Book {int_to_roman(ch['book_num']):>3}, "
                  f"Ch {int_to_roman(ch['chapter_num']):>5}: "
                  f"{wc:>5} words — {ch['chapter_title']}")
        print(f"\nFragments ({len(fragments)}):")
        for frag in fragments:
            text = clean_text('\n'.join(frag['lines']))
            wc = len(text.split()) if text else 0
            title = frag.get('title', '(untitled)')
            print(f"  Frag {frag['number']:>3}: {wc:>4} words — {title}")
        print(f"\nManual/Enchiridion ({len(manual_sections)} sections):")
        for sec in manual_sections:
            text = clean_text('\n'.join(sec['lines']))
            wc = len(text.split()) if text else 0
            print(f"  Section {sec['number']:>2}: {wc:>4} words")
        return

    # Build sections.
    sections = build_sections(preface_text, chapters, fragments, manual_sections)

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
    out_path = Path('discourses_long.json')
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nOutput written to: {out_path.resolve()}")
    print(f"Sections: {len(sections)}, Total words: {sum(s['word_count'] for s in sections)}")


if __name__ == '__main__':
    main()
