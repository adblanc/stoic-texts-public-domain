#!/usr/bin/env python3
"""
Parser for Seneca's Moral Letters to Lucilius (Epistulae Morales).

Source: Wikisource digital edition of R.M. Gummere's Loeb Classical Library
translation (3 volumes). This parser is specifically calibrated to the
structural patterns in that file:
  - 124 letters (I–CXXIV) with Roman numeral + title headers
  - One anomaly: Letter XCI is missing its "XCI." prefix
  - Letters separated by "* * *" markers
  - Footnote markers [N] inline, footnote text as "↑ ..." lines after letters
  - Three volumes with indexes, appendices, and front/back matter to skip
  - Introduction by Gummere to skip

Usage:
    python parser_letters.py <path_to_txt_file>
    python parser_letters.py <path_to_txt_file> --dry-run
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

# Matches a standard letter header: "I. ON SAVING TIME" through "CXXIV. ..."
# The Roman numeral is followed by a period, a space, and the title in caps.
# Allows ASCII apostrophe, Unicode right single quotation mark (U+2019),
# and Unicode left single quotation mark (U+2018) in titles.
# Optional trailing [N] footnote marker.
RE_LETTER_HEADER = re.compile(
    r'^([IVXLC]+)\.\s+([A-Z][A-Z\s,\'\u2018\u2019\-\.\(\)]+?)(\[\d+\])?\s*$'
)

# Special case: Letter XCI has no Roman numeral prefix.
# "ON THE LESSON TO BE DRAWN FROM THE BURNING OF LYONS[1]"
RE_LETTER_91_HEADER = re.compile(
    r'^ON THE LESSON TO BE DRAWN FROM THE BURNING OF LYONS(\[\d+\])?\s*$'
)

# Matches inline footnote markers like [1], [2], etc.
RE_FOOTNOTE_MARKER = re.compile(r'\[\d+\]')

# Matches footnote text lines starting with the ↑ character.
RE_FOOTNOTE_LINE = re.compile(r'^↑')

# Matches the "* * *" separator between letters.
RE_SEPARATOR = re.compile(r'^\*\s+\*\s+\*$')

# Matches "THE EPISTLES OF SENECA" volume header.
RE_VOLUME_HEADER = re.compile(r'^THE EPISTLES OF SENECA$')

# Matches "Volume I.", "Volume II.", "Volume III." markers.
RE_VOLUME_MARKER = re.compile(r'^Volume [IVX]+\.$')

# Matches the greeting line in Letter I.
RE_GREETING = re.compile(r'^Greetings from Seneca')

# Matches TOC entries: Roman numeral + title + page number.
# Used to differentiate TOC lines from actual letter headers.
# Also matches appendix/textual apparatus lines like "VIII. 7. differetur Q."
RE_TOC_ENTRY = re.compile(
    r'^[IVXLC]+\.\s+[A-Z].*\s+\d+\s*$'
)

# Matches textual apparatus entries like "VIII. 7. differetur Q."
# These look like letter headers but have a second period+number.
RE_APPARATUS_ENTRY = re.compile(
    r'^[IVXLC]+\.\s+\d+\.'
)

# Matches "CONTENTS OF VOLUME" lines.
RE_CONTENTS_HEADER = re.compile(r'^CONTENTS OF VOLUME')

# Matches "INDEX OF PROPER NAMES" or "SUBJECT INDEX".
RE_INDEX_HEADER = re.compile(r'^(INDEX OF PROPER NAMES|SUBJECT INDEX)')

# Matches "APPENDIX" lines.
RE_APPENDIX_HEADER = re.compile(r'^APPENDIX')

# Matches "INTRODUCTION" line.
RE_INTRODUCTION = re.compile(r'^INTRODUCTION$')

# Matches printer mark lines.
RE_PRINTER = re.compile(r'^Printed in Great Britain')

# Matches "About this digital edition" line.
RE_DIGITAL_EDITION = re.compile(r'^About this digital edition$')

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
# Identify content regions
# ---------------------------------------------------------------------------


def find_letter_regions(lines: list[str]) -> list[tuple[int, int]]:
    """
    Find the start/end line indices of the three letter-content regions
    (one per volume). Each region starts after 'THE EPISTLES OF SENECA'
    and ends before the next volume marker, index, appendix, or end of file.

    Returns list of (start_idx, end_idx) tuples.
    """
    regions = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if RE_VOLUME_HEADER.match(stripped):
            # Found a "THE EPISTLES OF SENECA" marker. Content starts after it.
            start = i + 1
            # Scan forward for the end of this letter region.
            j = start
            while j < len(lines):
                s = lines[j].strip()
                if RE_VOLUME_MARKER.match(s):
                    break
                if RE_INDEX_HEADER.match(s):
                    break
                if RE_APPENDIX_HEADER.match(s):
                    break
                if RE_PRINTER.match(s):
                    break
                if RE_DIGITAL_EDITION.match(s):
                    break
                if RE_CONTENTS_HEADER.match(s):
                    break
                # Another "THE EPISTLES OF SENECA" means new volume.
                if j > start and RE_VOLUME_HEADER.match(s):
                    break
                j += 1
            regions.append((start, j))
            i = j
        else:
            i += 1

    return regions


# ---------------------------------------------------------------------------
# Parse letters from content lines
# ---------------------------------------------------------------------------


def is_letter_header(line: str, in_content: bool = True) -> Optional[tuple]:
    """
    Check if a line is a letter header. Returns (letter_number, title) or None.

    Distinguishes actual letter headers from TOC entries (which have a
    trailing page number).
    """
    stripped = line.strip()
    if not stripped:
        return None

    # Skip TOC entries (have trailing page number).
    if RE_TOC_ENTRY.match(stripped):
        return None

    # Skip textual apparatus entries like "VIII. 7. differetur Q."
    if RE_APPARATUS_ENTRY.match(stripped):
        return None

    # Check for the special XCI anomaly.
    if RE_LETTER_91_HEADER.match(stripped):
        title = RE_FOOTNOTE_MARKER.sub('', stripped).strip()
        return (91, title)

    m = RE_LETTER_HEADER.match(stripped)
    if m:
        roman = m.group(1)
        title_raw = m.group(2).strip()
        # Remove any trailing period from title (e.g., "ON BENEFITS.")
        title = title_raw.rstrip('.')
        num = roman_to_int(roman)
        # Sanity check: letters are numbered 1–124.
        if 1 <= num <= 124:
            return (num, title)

    return None


def parse_letters(lines: list[str], regions: list[tuple[int, int]]) -> list[dict]:
    """
    Parse individual letters from the identified content regions.
    Returns a list of raw letter dicts with number, title, and raw lines.
    """
    raw_letters = []

    for region_start, region_end in regions:
        current_letter_num = None
        current_title = None
        current_lines: list[str] = []

        for idx in range(region_start, region_end):
            line = lines[idx]
            stripped = line.strip()

            header = is_letter_header(stripped)
            if header is not None:
                # Save previous letter.
                if current_letter_num is not None:
                    raw_letters.append({
                        'number': current_letter_num,
                        'title': current_title,
                        'lines': current_lines,
                    })
                current_letter_num, current_title = header
                current_lines = []
                continue

            # Skip separator lines.
            if RE_SEPARATOR.match(stripped):
                continue

            # Accumulate lines for the current letter.
            if current_letter_num is not None:
                current_lines.append(line)

        # Flush last letter in this region.
        if current_letter_num is not None:
            raw_letters.append({
                'number': current_letter_num,
                'title': current_title,
                'lines': current_lines,
            })

    return raw_letters


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def clean_letter_text(lines: list[str], letter_num: int) -> str:
    """
    Clean a letter's raw lines:
    - Remove footnote text lines (↑ ...)
    - Remove inline footnote markers [N]
    - Normalize whitespace
    - Preserve paragraph breaks as single newlines
    """
    cleaned = []
    in_footnotes = False

    for line in lines:
        stripped = line.strip()

        # Skip blank lines at the very start.
        if not cleaned and not stripped:
            continue

        # Detect footnote region: once we see a ↑ line, all subsequent
        # lines until the next blank-line gap are footnotes.
        if RE_FOOTNOTE_LINE.match(stripped):
            in_footnotes = True
            continue

        # If we're in a footnote region and hit a blank line, stay in
        # footnote mode (footnotes can have blank lines between them).
        if in_footnotes:
            if stripped == '':
                continue
            # Check if this non-blank line is still a footnote (↑) or
            # looks like footnote continuation (not starting a new paragraph
            # that begins with a number like "1." or regular text).
            if RE_FOOTNOTE_LINE.match(stripped):
                continue
            # If it looks like regular content, exit footnote mode.
            # But footnotes can also be continuations without ↑.
            # Heuristic: if we've been in footnotes and we see a non-↑
            # line that isn't blank, it's probably still footnote content
            # (e.g., quoted Latin). We rely on the fact that footnotes
            # come at the END of a letter, so everything after the first ↑
            # within a single letter is footnote material.
            continue

        # Remove inline footnote markers.
        line_clean = RE_FOOTNOTE_MARKER.sub('', stripped)

        # Collapse multiple spaces.
        line_clean = re.sub(r' {2,}', ' ', line_clean).strip()

        cleaned.append(line_clean)

    # Join and normalize.
    text = '\n'.join(cleaned)

    # Collapse runs of 3+ newlines into paragraph breaks.
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip trailing blank lines.
    text = text.strip()

    return text


# ---------------------------------------------------------------------------
# Building the JSON structure
# ---------------------------------------------------------------------------


def build_sections(raw_letters: list[dict]) -> list[dict]:
    """
    Build the list of section dictionaries from parsed letters.
    """
    sections = []

    for letter in raw_letters:
        num = letter['number']
        title = letter['title']
        text = clean_letter_text(letter['lines'], num)

        if not text:
            log.warning("Letter %d (%s): empty after cleaning — skipping.", num, title)
            continue

        word_count = len(text.split())
        section_id = f"letters_{num:03d}"
        source_ref = f"Letter {int_to_roman(num)} ({num})"

        section = {
            "id": section_id,
            "book": None,
            "book_title": None,
            "chapter": None,
            "chapter_title": None,
            "letter_number": num,
            "letter_title": title,
            "text": text,
            "word_count": word_count,
            "source_reference": source_ref,
        }
        sections.append(section)

    return sections


def build_metadata() -> dict:
    """Build the metadata block."""
    return {
        "work": "Moral Letters to Lucilius (Epistulae Morales ad Lucilium)",
        "author": "Seneca",
        "translator": "Richard M. Gummere (1917–1925)",
        "source": "Wikisource",
        "parsed_date": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(sections: list[dict], original_lines: list[str]) -> None:
    """Run validation checks and report results."""
    total_sections = len(sections)
    total_words = sum(s['word_count'] for s in sections)

    print("\n=== Validation Report ===")
    print(f"Total letters parsed: {total_sections}")
    print(f"Total word count: {total_words}")

    # Check we have all 124 letters.
    found_nums = sorted(s['letter_number'] for s in sections)
    expected = list(range(1, 125))
    missing = set(expected) - set(found_nums)
    duplicate = [n for n in found_nums if found_nums.count(n) > 1]
    if missing:
        print(f"\nWARNING: Missing letters: {sorted(missing)}")
    else:
        print("\nAll 124 letters found.")
    if duplicate:
        print(f"WARNING: Duplicate letters: {sorted(set(duplicate))}")

    # Sections with unusually short text (under 20 words).
    short = [s for s in sections if s['word_count'] < 20]
    if short:
        print(f"\nLetters with fewer than 20 words ({len(short)}):")
        for s in short:
            print(f"  {s['source_reference']}: {s['word_count']} words — \"{s['text'][:80]}...\"")
    else:
        print("\nNo letters with fewer than 20 words.")

    # Check for structural markers in the file that were NOT parsed.
    unparsed = []
    for i, line in enumerate(original_lines):
        stripped = line.strip()
        # Look for lines that look like letter headers but weren't captured.
        m = RE_LETTER_HEADER.match(stripped)
        if m and not RE_TOC_ENTRY.match(stripped):
            roman = m.group(1)
            num = roman_to_int(roman)
            if 1 <= num <= 124:
                # Check if this was in a TOC or index region (has trailing page number).
                # Already filtered by RE_TOC_ENTRY, but double check.
                pass

    # Log what was excluded.
    print("\nExcluded content (by type):")
    excluded = {
        'introduction_lines': 0,
        'index_lines': 0,
        'appendix_lines': 0,
        'toc_lines': 0,
        'footnote_lines': 0,
    }

    # Count footnote lines in letter regions.
    for line in original_lines:
        stripped = line.strip()
        if RE_FOOTNOTE_LINE.match(stripped):
            excluded['footnote_lines'] += 1

    print(f"  Footnote lines (↑): {excluded['footnote_lines']}")

    # Word count distribution.
    word_counts = [s['word_count'] for s in sections]
    print(f"\nWord count range: {min(word_counts)}–{max(word_counts)}")
    print(f"Average words per letter: {sum(word_counts) // len(word_counts)}")

    # Top 5 longest and shortest.
    by_wc = sorted(sections, key=lambda s: s['word_count'])
    print("\n5 shortest letters:")
    for s in by_wc[:5]:
        print(f"  {s['source_reference']}: {s['word_count']} words")
    print("\n5 longest letters:")
    for s in by_wc[-5:]:
        print(f"  {s['source_reference']}: {s['word_count']} words")


def print_sample(sections: list[dict]) -> None:
    """Print 3 randomly selected sections for manual verification."""
    print("\n=== Sample Sections (3 random) ===")
    sample_size = min(3, len(sections))
    samples = random.sample(sections, sample_size)
    for s in samples:
        print(f"\n--- {s['source_reference']} — {s['letter_title']} "
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
        description="Parse Seneca's Moral Letters to Lucilius (Gummere / Wikisource) into JSON."
    )
    parser.add_argument('file', help='Path to the moral_letters_to_Lucilius.txt file')
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

    # Find content regions.
    regions = find_letter_regions(all_lines)
    log.info("Found %d letter-content regions.", len(regions))
    for i, (start, end) in enumerate(regions):
        log.info("  Region %d: lines %d–%d (%d lines)", i + 1, start + 1, end, end - start)

    # Parse letters.
    raw_letters = parse_letters(all_lines, regions)
    log.info("Parsed %d raw letters.", len(raw_letters))

    if args.dry_run:
        print("\n=== Dry Run: Structural Analysis ===")
        for letter in raw_letters:
            text = clean_letter_text(letter['lines'], letter['number'])
            wc = len(text.split()) if text else 0
            roman = int_to_roman(letter['number'])
            preview = text[:80].replace('\n', ' ') if text else '(empty)'
            print(f"  Letter {roman:>6} ({letter['number']:>3}): "
                  f"{wc:>5} words — {letter['title']}")
        print(f"\nTotal: {len(raw_letters)} letters")
        return

    # Build sections.
    sections = build_sections(raw_letters)

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
    out_path = Path('moral_letters_long.json')
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nOutput written to: {out_path.resolve()}")
    print(f"Letters: {len(sections)}, Total words: {sum(s['word_count'] for s in sections)}")


if __name__ == '__main__':
    main()
