#!/usr/bin/env python3
"""
Parser for Seneca's On the Shortness of Life (De Brevitate Vitae).

Source: Wikisource digital edition of John W. Basore's Loeb Classical Library
translation (1932). This parser is specifically calibrated to the structural
patterns in that file:
  - 20 chapters numbered with Arabic numerals (1–20)
  - Chapter headers: digit + period at line start (e.g., "1. The majority...")
  - One anomaly: Chapter 4 has no space after the period ("4.You will see...")
  - Each chapter followed by a "Footnotes" header + "↑ ..." lines
  - Inline footnote markers [1], [2], etc.
  - Multi-paragraph chapters (paragraphs separated by blank lines)
  - Header block (title, author, year, dedication) + TOC to skip
  - Greek text in footnotes (Unicode)

Usage:
    python parser_shortness.py <path_to_txt_file>
    python parser_shortness.py <path_to_txt_file> --dry-run
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

# Matches a chapter header: Arabic number + period at line start.
# The number is captured. Allows optional space after the period
# (chapter 4 has "4.You" with no space).
RE_CHAPTER_HEADER = re.compile(
    r'^(\d+)\.\s*(.*)'
)

# Matches the "Footnotes" header that appears after each chapter.
RE_FOOTNOTES_HEADER = re.compile(
    r'^Footnotes\s*$'
)

# Matches footnote text lines starting with the ↑ character.
RE_FOOTNOTE_LINE = re.compile(r'^↑')

# Matches inline footnote markers like [1], [2], etc.
RE_FOOTNOTE_MARKER = re.compile(r'\[\d+\]')

# Matches the "Contents" line in the TOC.
RE_CONTENTS = re.compile(r'^Contents\s*$')

# Matches TOC entries like "Chapter I", "Chapter II", etc.
RE_TOC_CHAPTER = re.compile(r'^Chapter [IVXLC]+\s*$')

# Matches the file header title line.
RE_TITLE = re.compile(r'^ON THE SHORTNESS OF LIFE\s*$')

# Matches the dedication line.
RE_DEDICATION = re.compile(r'^TO PAULINUS\s*$')

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
# Find the start of chapter content (skip header + TOC)
# ---------------------------------------------------------------------------


def find_content_start(lines):
    """
    Find the line index where actual chapter content begins.
    Skips the header block (title, author, year, dedication),
    the "ON THE SHORTNESS OF LIFE" title, and the TOC
    ("Contents" followed by "Chapter I" through "Chapter XX").

    Returns the index of the first chapter header line.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = RE_CHAPTER_HEADER.match(stripped)
        if m:
            num = int(m.group(1))
            if num == 1:
                return i

    log.error("Could not find the start of chapter 1.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Parse chapters
# ---------------------------------------------------------------------------


def parse_chapters(lines, start_idx):
    """
    Parse individual chapters from the content lines.
    Returns a list of raw chapter dicts with number and raw lines.
    """
    raw_chapters = []
    current_num = None
    current_first_line_text = None
    current_lines = []
    in_footnotes = False

    for i in range(start_idx, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Check for "Footnotes" header — marks end of chapter text.
        if RE_FOOTNOTES_HEADER.match(stripped):
            in_footnotes = True
            continue

        # While in footnotes, skip everything until next chapter header.
        if in_footnotes:
            # Check if this line starts a new chapter.
            m = RE_CHAPTER_HEADER.match(stripped)
            if m:
                num = int(m.group(1))
                # Must be a plausible next chapter (1–20).
                if 1 <= num <= 20:
                    # Save previous chapter.
                    if current_num is not None:
                        raw_chapters.append({
                            'number': current_num,
                            'first_line_text': current_first_line_text,
                            'lines': current_lines,
                        })
                    current_num = num
                    current_first_line_text = m.group(2)
                    current_lines = []
                    in_footnotes = False
                    continue
            # Otherwise skip footnote content.
            continue

        # Check for chapter header (not in footnotes).
        m = RE_CHAPTER_HEADER.match(stripped)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 20:
                # Save previous chapter.
                if current_num is not None:
                    raw_chapters.append({
                        'number': current_num,
                        'first_line_text': current_first_line_text,
                        'lines': current_lines,
                    })
                current_num = num
                current_first_line_text = m.group(2)
                current_lines = []
                continue

        # Accumulate lines for the current chapter.
        if current_num is not None and not in_footnotes:
            current_lines.append(line)

    # Flush last chapter.
    if current_num is not None:
        raw_chapters.append({
            'number': current_num,
            'first_line_text': current_first_line_text,
            'lines': current_lines,
        })

    return raw_chapters


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def clean_chapter_text(first_line_text, lines):
    """
    Clean a chapter's raw lines:
    - Prepend the first line text (captured from chapter header line)
    - Remove inline footnote markers [N]
    - Normalize whitespace
    - Preserve paragraph breaks as double newlines
    - Strip any remaining footnote content that wasn't caught
    """
    # Start with the first line text from the header line itself.
    all_lines = []
    if first_line_text:
        all_lines.append(first_line_text)

    for line in lines:
        stripped = line.strip()
        # Stop if we hit a "Footnotes" header (safety net).
        if RE_FOOTNOTES_HEADER.match(stripped):
            break
        # Skip footnote content lines (safety net).
        if RE_FOOTNOTE_LINE.match(stripped):
            break
        all_lines.append(stripped)

    # Remove trailing blank lines.
    while all_lines and not all_lines[-1]:
        all_lines.pop()

    # Remove leading blank lines.
    while all_lines and not all_lines[0]:
        all_lines.pop(0)

    # Group into paragraphs: consecutive non-blank lines form one paragraph.
    paragraphs = []
    current_para = []

    for line in all_lines:
        if line == '':
            if current_para:
                paragraphs.append(' '.join(current_para))
                current_para = []
        else:
            # Remove inline footnote markers.
            cleaned = RE_FOOTNOTE_MARKER.sub('', line)
            # Collapse multiple spaces.
            cleaned = re.sub(r' {2,}', ' ', cleaned).strip()
            if cleaned:
                current_para.append(cleaned)

    if current_para:
        paragraphs.append(' '.join(current_para))

    text = '\n\n'.join(paragraphs)
    return text


# ---------------------------------------------------------------------------
# Building the JSON structure
# ---------------------------------------------------------------------------


def build_sections(raw_chapters):
    """
    Build the list of section dictionaries from parsed chapters.
    """
    sections = []

    for chapter in raw_chapters:
        num = chapter['number']
        text = clean_chapter_text(chapter['first_line_text'], chapter['lines'])

        if not text:
            log.warning("Chapter %d: empty after cleaning — skipping.", num)
            continue

        word_count = len(text.split())
        section_id = f"shortness_ch{num:02d}"
        source_ref = f"Chapter {num}"

        section = {
            "id": section_id,
            "book": None,
            "book_title": None,
            "chapter": num,
            "chapter_title": None,
            "letter_number": None,
            "text": text,
            "word_count": word_count,
            "source_reference": source_ref,
        }
        sections.append(section)

    return sections


def build_metadata():
    """Build the metadata block."""
    return {
        "work": "On the Shortness of Life (De Brevitate Vitae)",
        "author": "Seneca",
        "translator": "John W. Basore (1932)",
        "source": "Wikisource",
        "parsed_date": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(sections, original_lines):
    """Run validation checks and report results."""
    total_sections = len(sections)
    total_words = sum(s['word_count'] for s in sections)

    print("\n=== Validation Report ===")
    print(f"Total chapters parsed: {total_sections}")
    print(f"Total word count: {total_words}")

    # Check we have all 20 chapters.
    found_nums = sorted(s['chapter'] for s in sections)
    expected = list(range(1, 21))
    missing = set(expected) - set(found_nums)
    duplicate = [n for n in found_nums if found_nums.count(n) > 1]
    if missing:
        print(f"\nWARNING: Missing chapters: {sorted(missing)}")
    else:
        print("\nAll 20 chapters found.")
    if duplicate:
        print(f"WARNING: Duplicate chapters: {sorted(set(duplicate))}")

    # Sections with unusually short text (under 20 words).
    short = [s for s in sections if s['word_count'] < 20]
    if short:
        print(f"\nChapters with fewer than 20 words ({len(short)}):")
        for s in short:
            print(f"  {s['source_reference']}: {s['word_count']} words — "
                  f"\"{s['text'][:80]}...\"")
    else:
        print("\nNo chapters with fewer than 20 words.")

    # Check for unparsed footnote markers in output text.
    unparsed_fn = []
    for s in sections:
        markers = RE_FOOTNOTE_MARKER.findall(s['text'])
        if markers:
            unparsed_fn.append((s['source_reference'], markers))
    if unparsed_fn:
        print(f"\nWARNING: Unparsed footnote markers found in {len(unparsed_fn)} sections:")
        for ref, markers in unparsed_fn:
            print(f"  {ref}: {markers}")
    else:
        print("\nNo unparsed footnote markers in output text.")

    # Check for ↑ characters in output text.
    arrow_in_text = [s for s in sections if '↑' in s['text']]
    if arrow_in_text:
        print(f"\nWARNING: ↑ character found in {len(arrow_in_text)} sections' text.")
    else:
        print("No ↑ characters leaked into output text.")

    # Count footnote lines in source.
    fn_lines = sum(1 for l in original_lines if RE_FOOTNOTE_LINE.match(l.strip()))
    print(f"\nExcluded content:")
    print(f"  Footnote lines (↑): {fn_lines}")

    # Word count distribution.
    word_counts = [s['word_count'] for s in sections]
    print(f"\nWord count range: {min(word_counts)}–{max(word_counts)}")
    print(f"Average words per chapter: {sum(word_counts) // len(word_counts)}")

    # Top 5 longest and shortest.
    by_wc = sorted(sections, key=lambda s: s['word_count'])
    print("\n5 shortest chapters:")
    for s in by_wc[:5]:
        print(f"  {s['source_reference']}: {s['word_count']} words")
    print("\n5 longest chapters:")
    for s in by_wc[-5:]:
        print(f"  {s['source_reference']}: {s['word_count']} words")


def print_sample(sections):
    """Print 3 randomly selected sections for manual verification."""
    print("\n=== Sample Sections (3 random) ===")
    sample_size = min(3, len(sections))
    samples = random.sample(sections, sample_size)
    for s in samples:
        print(f"\n--- {s['source_reference']} "
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
        description="Parse Seneca's On the Shortness of Life (Basore / Wikisource) into JSON."
    )
    parser.add_argument('file', help='Path to the on_the_shortness_of_life.txt file')
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

    # Find where content begins (skip header + TOC).
    content_start = find_content_start(all_lines)
    log.info("Content starts at line %d.", content_start + 1)

    # Parse chapters.
    raw_chapters = parse_chapters(all_lines, content_start)
    log.info("Parsed %d raw chapters.", len(raw_chapters))

    if args.dry_run:
        print("\n=== Dry Run: Structural Analysis ===")
        for chapter in raw_chapters:
            text = clean_chapter_text(chapter['first_line_text'], chapter['lines'])
            wc = len(text.split()) if text else 0
            preview = text[:80].replace('\n', ' ') if text else '(empty)'
            print(f"  Chapter {chapter['number']:>2}: {wc:>5} words — \"{preview}...\"")
        print(f"\nTotal: {len(raw_chapters)} chapters")
        return

    # Build sections.
    sections = build_sections(raw_chapters)

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
    out_path = Path('on_the_shortness_of_life_long.json')
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    print(f"\nOutput written to: {out_path.resolve()}")
    print(f"Chapters: {len(sections)}, Total words: {sum(s['word_count'] for s in sections)}")


if __name__ == '__main__':
    main()
