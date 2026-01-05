"""
📚 Reference Verification Tool - Streamlit Version
Supports: APA, Chicago, Harvard, IEEE, ACM, MLA
"""

import streamlit as st
import re
import time
import requests
import pandas as pd
from rapidfuzz import fuzz
import string
from io import BytesIO

# ================= Configuration =================
OPENALEX_BASE = "https://api.openalex.org/works"
CROSSREF_BASE = "https://api.crossref.org/works"

HEADERS = {
    "User-Agent": "ReferenceVerificationTool/1.0 (mailto:your_email@example.com)",
}

TITLE_THRESHOLD = 85
TITLE_MISMATCH_THRESHOLD = 70
REQUEST_DELAY = 0.2


# ================= Helper Functions =================

def normalize_doi(doi):
    """Normalize DOI by removing URL prefix and trailing punctuation"""
    if not doi:
        return None
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi, flags=re.I)
    doi = re.sub(r'^doi:', '', doi, flags=re.I)
    doi = doi.rstrip('.,;)')
    return doi.lower().strip()


def extract_surname(author_name):
    """Extract surname from author name in various formats"""
    if not author_name:
        return ""

    author_name = re.sub(r'\(\d{4}\)', '', author_name).strip()
    author_name = re.sub(r'^\[\d+\]\s*', '', author_name)

    if ',' in author_name:
        surname = author_name.split(',')[0].strip()
        return surname.lower() if surname else ""

    parts = author_name.split()
    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0].lower()

    last_part = parts[-1].replace('.', '').strip()
    if len(last_part) <= 2 and (last_part.isupper() or len(last_part) == 1):
        return parts[0].lower()
    else:
        return parts[-1].lower()


def normalize_page_range(page_range):
    """Normalize page range to consistent format"""
    if not page_range or page_range == "-":
        return "-"
    normalized = str(page_range).replace('–', '-').replace('—', '-').replace('−', '-')
    normalized = re.sub(r'\s*-\s*', '-', normalized)
    return normalized.strip()


def standardize_title(title):
    """Standardize title for comparison (lowercase, no punctuation)"""
    if not title:
        return ""
    title = title.lower()
    title = title.replace("u.k.", "uk").replace("u.s.", "us")
    title = title.translate(str.maketrans('', '', string.punctuation))
    title = re.sub(r'\s+', ' ', title).strip()
    return title


# ================= Reference Parser =================

def parse_reference(raw_ref):
    """Parse mainstream citation formats"""

    text = re.sub(r'^[\[\(\{]?\d+[\]\)\}]\.?\s*', '', raw_ref.strip())

    # DOI Extraction
    doi_match = re.search(
        r'(?:https?://)?(?:doi\.org/|DOI:?\s*)?(10\.\d{4,9}/[^\s"\'<>\]]+)',
        text, re.I
    )
    doi = doi_match.group(1).rstrip('.,;)]') if doi_match else None

    # Year Extraction
    year = None
    year_in_parentheses = False

    year_match = re.search(r'\((\d{4})[a-z]?\)', text)
    if year_match:
        year = int(year_match.group(1))
        year_in_parentheses = True
    else:
        year_match = re.search(r'\.\s+(\d{4})\.\s+[\u201c\u201d"\'A-Z]', text)
        if year_match:
            year = int(year_match.group(1))
            year_in_parentheses = False
        else:
            year_match = re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{4})', text, re.I)
            if year_match:
                year = int(year_match.group(1))
            else:
                year_match = re.search(r',\s*(\d{4})\.?\s*(?:doi|$)', text, re.I)
                if year_match:
                    year = int(year_match.group(1))
                else:
                    year_match = re.search(r'[,\s](\d{4})[;,\.]', text)
                    if year_match:
                        year = int(year_match.group(1))

    # Detect Format Type
    is_ieee_style = bool(re.search(r'(?:vol\.\s*\d+.*?no\.\s*\d+|IEEE\s+\w+|\d+\(\d+\):\d+)', text, re.I))
    is_vancouver = bool(re.search(r';\d+\(\d+\):', text))
    has_quotes = bool(re.search(r'[\u201c\u201d\u2018\u2019"\'"]', text))

    # Title Extraction
    title = "Unknown"

    # Pattern 1: Quoted title
    if has_quotes:
        quote_patterns = [
            r'\u201c([^\u201d]+)\u201d',
            r'\u2018([^\u2019]+)\u2019',
            r'"([^"]+)"',
            r"'([^']+)'",
            r'[\u201c\u201d"\u2018\u2019\'](.+?)[\u201c\u201d"\u2018\u2019\']',
        ]
        for pattern in quote_patterns:
            quote_match = re.search(pattern, text)
            if quote_match:
                title = quote_match.group(1).strip()
                break

    # Pattern 2: IEEE format WITHOUT quotes
    if title == "Unknown" and is_ieee_style:
        last_author_match = re.search(r'\band\s+[A-Z][\w\s\.]+?\.\s+', text)

        if last_author_match:
            after_authors = text[last_author_match.end():]

            title_patterns = [
                r'^([A-Z][^\.]+?)\.\s+[A-Z][\w\s&]+?,?\s*vol\.',
                r'^([A-Z][^\.]+?)\.\s+[A-Z][\w\s&]+?,\s*\d+\(',
                r'^([A-Z][^\.]{20,}?)\.\s+IEEE',
            ]

            for pattern in title_patterns:
                title_match = re.search(pattern, after_authors, re.I)
                if title_match:
                    title = title_match.group(1).strip()
                    break

        if title == "Unknown":
            fallback_patterns = [
                r'\.\s+([A-Z][a-z][\w\s:,\-]{20,}?)\.\s+IEEE',
                r'\.\s+([A-Z][a-z][\w\s:,\-]{20,}?)\.\s+[A-Z][\w\s&]+?,\s*\d+\(',
                r'\.\s+([A-Z][a-z][\w\s:,\-]{20,}?)\.\s+[A-Z][\w\s&]+?,?\s*vol\.',
            ]

            for pattern in fallback_patterns:
                fallback_match = re.search(pattern, text, re.I)
                if fallback_match:
                    potential_title = fallback_match.group(1).strip()
                    if not re.search(r'\b[A-Z]{1,3}\s+[A-Z][a-z]+\b', potential_title[:40]):
                        title = potential_title
                        break

    # Pattern 3: Year WITHOUT parentheses
    if title == "Unknown" and not year_in_parentheses and year:
        title_match = re.search(rf'\.\s+{year}\.\s+[\u201c\u201d"\'"]?(.+?)[\u201c\u201d"\'"]?[\.?!]\s+[A-Z]', text)
        if title_match:
            title = title_match.group(1).strip()
        else:
            title_match = re.search(rf'\.\s+{year}\.\s+(.+?)\.\s+[A-Z][A-Za-z\s]+\s+\d+', text)
            if title_match:
                title = title_match.group(1).strip()

    # Pattern 4: Year WITH parentheses
    if title == "Unknown" and year_in_parentheses:
        patterns = [
            r'\(\d{4}\)\.\s*(.+?)[\.?!]\s+[A-Z]',
            r'\(\d{4}\)\s+(.+?)[\.?!]\s+[A-Z]',
            r'\(\d{4}\)\s*\.?\s*(.+?)[\.?!]\s*(?:In\s+)?(?:Proceedings?|Conference)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                potential_title = match.group(1).strip()
                if len(potential_title) > 10:
                    title = potential_title
                    break

    # Pattern 5: Vancouver style
    if title == "Unknown" and is_vancouver:
        title_match = re.search(r'\.(.+?)[\.?!]\s+[A-Z][^\.]+\s+\d{4}', text)
        if title_match:
            title = title_match.group(1).strip()

    # Clean title
    if title != "Unknown":
        title = title.replace('\u201c', '').replace('\u201d', '')
        title = title.replace('\u2018', '').replace('\u2019', '')
        title = title.replace('"', '').replace("'", '')
        title = title.strip()

    # Journal/Source Extraction
    journal = "Unknown"

    if is_ieee_style:
        if has_quotes:
            parts = re.split(r'[\u201c\u201d\u2018\u2019"\'"]', text)
            after_quote = parts[-1] if len(parts) > 1 else text
            ieee_match = re.search(r',\s*([^,]+?),\s*(?:vol\.|\d+\()', after_quote, re.I)
            if ieee_match:
                journal = ieee_match.group(1).strip()
        else:
            ieee_patterns = [
                r'\.\s+([A-Z][A-Za-z\s&]+?),\s*vol\.',
                r'\.\s+([A-Z][A-Za-z\s&]+?),\s*\d+\(',
            ]
            for pattern in ieee_patterns:
                ieee_match = re.search(pattern, text, re.I)
                if ieee_match:
                    potential_journal = ieee_match.group(1).strip()
                    if len(potential_journal) < 100:
                        journal = potential_journal
                        break

    elif is_vancouver:
        vanc_match = re.search(r'\.([^\.]+)\.\s*\d{4};', text)
        if vanc_match:
            journal = vanc_match.group(1).strip()

    else:
        patterns = [
            r'[\.?!]\s*[\u201c\u201d"\'"]?([A-Za-z\s&]+?)[\u201c\u201d"\'"]?\s*,?\s*\d+\s*\(',
            r'[\.?!]\s+([A-Z][A-Za-z\s&]+?)\s+\d+\s*\(',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                journal = match.group(1).strip()
                break

    # Volume/Issue/Pages
    volume, issue, page_range = "-", "-", "-"

    if 'vol.' in text.lower():
        vol_match = re.search(r'vol\.\s*(\d+)', text, re.I)
        issue_match = re.search(r'no\.\s*(\d+)', text, re.I)
        page_match = re.search(r'pp\.\s*([\d–\-—]+)', text, re.I)

        if vol_match:
            volume = vol_match.group(1)
        if issue_match:
            issue = issue_match.group(1)
        if page_match:
            page_range = normalize_page_range(page_match.group(1))

    if page_range == "-":
        patterns = [
            (r'(\d+)\s*\((\d+)\):\s*([\d–\-—]+)', True),
            (r',\s*(\d+)\s*\((\d+)\),\s*([\d–\-—]+)', True),
            (r'\d{4};(\d+)\((\d+)\):([\d–\-—]+)', True),
            (r'\s(\d+)\s*\((\d+)\):\s*([\d–\-—]+)', True),
            (r'\s(\d+):\s*([\d–\-—]+)', False),
        ]

        for pattern, has_issue in patterns:
            match = re.search(pattern, text)
            if match:
                if has_issue:
                    volume, issue, page_range = match.groups()
                else:
                    volume = match.group(1)
                    issue = "-"
                    page_range = match.group(2)
                page_range = normalize_page_range(page_range)
                break

    if page_range == "-":
        pp_match = re.search(r'pp\.\s*([\d–\-—]+)', text, re.I)
        if pp_match:
            page_range = normalize_page_range(pp_match.group(1))

    # First Author
    if ',' in raw_ref:
        first_author = raw_ref.split(',')[0].strip()
    else:
        if year:
            if year_in_parentheses:
                year_str = f"({year})"
            else:
                year_str = f". {year}."

            year_pos = raw_ref.find(year_str)
            if year_pos > 0:
                first_author = raw_ref[:year_pos].strip()
            else:
                first_author = raw_ref.split('.')[0].strip() if '.' in raw_ref else raw_ref.split()[0]
        else:
            first_author = raw_ref.split('.')[0].strip() if '.' in raw_ref else raw_ref.split()[0]

    first_author = re.sub(r'^\[\d+\]\s*', '', first_author)
    first_author = re.sub(r'\(\d{4}\)', '', first_author).strip()
    first_author = re.sub(r'\.\s*\d{4}\.', '', first_author).strip()

    return {
        "raw_reference": raw_ref,
        "ref_title": title,
        "ref_first_author": first_author,
        "ref_year": year,
        "ref_journal": journal,
        "ref_volume": volume,
        "ref_issue": issue,
        "ref_page_range": page_range,
        "doi": doi
    }


# ================= API Functions =================

def query_openalex_by_doi(doi):
    if not doi:
        return None
    try:
        normalized = normalize_doi(doi)
        url = f"{OPENALEX_BASE}/doi:{normalized}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.json() if r.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None


def query_openalex_by_title(title, max_results=10):
    if not title or title == "Unknown":
        return []
    try:
        t = title.lower()
        t = re.sub(r'[&:?,;]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        words = t.split()
        t_short = " ".join(words[:8])

        params = {"filter": f"title.search:{t_short}", "per-page": max_results}
        r = requests.get(OPENALEX_BASE, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("results", [])
        return []
    except requests.exceptions.RequestException:
        return []


def extract_openalex_metadata(record):
    authorships = record.get("authorships", [])
    full_author_list = ", ".join([a['author']['display_name'] for a in authorships]) or "Unknown"
    first_author = authorships[0]['author']['display_name'] if authorships else "Unknown"

    biblio = record.get("biblio", {})
    primary = record.get("primary_location", {})

    title = record.get("title", "Unknown")
    year = record.get("publication_year", "Unknown")

    source_name = "Unknown"
    if primary.get("source"):
        source_name = primary["source"].get("display_name", "Unknown")
    elif biblio.get("journal_name"):
        source_name = biblio.get("journal_name")

    volume = biblio.get("volume") or "-"
    issue = biblio.get("issue") or "-"

    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    if first_page and last_page:
        page_range = f"{first_page}-{last_page}" if first_page != last_page else str(first_page)
    else:
        page_range = first_page or "-"

    page_range = normalize_page_range(page_range)

    raw_oa_doi = record.get("doi")
    if raw_oa_doi:
        m = re.search(r'(10\.\d{4,9}/[^\s"\'<>]+)', raw_oa_doi, re.I)
        oa_doi_plain = m.group(1).rstrip('.,;)') if m else raw_oa_doi
    else:
        oa_doi_plain = None

    is_retracted = record.get("is_retracted", False)

    doc_type_raw = record.get("type", "unknown")
    doc_type_map = {
        "article": "Journal Article",
        "book-chapter": "Book Chapter",
        "proceedings-article": "Conference Paper",
        "posted-content": "Preprint",
        "dataset": "Dataset",
        "book": "Book",
        "dissertation": "Dissertation",
        "unknown": "Unknown"
    }
    doc_type = doc_type_map.get(doc_type_raw, doc_type_raw.replace("-", " ").title())

    return {
        "oa_full_author": full_author_list,
        "oa_first_author": first_author,
        "oa_title": title,
        "oa_year": year,
        "oa_journal": source_name,
        "oa_volume": volume,
        "oa_issue": issue,
        "oa_page_range": page_range,
        "openalex_id": record.get("id", "Unknown"),
        "oa_doi": oa_doi_plain,
        "is_retracted": is_retracted,
        "doc_type": doc_type,
        "data_source": "OpenAlex"
    }


def query_crossref_by_doi(doi):
    if not doi:
        return None
    try:
        normalized = normalize_doi(doi)
        url = f"{CROSSREF_BASE}/{normalized}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("message")
        return None
    except requests.exceptions.RequestException:
        return None


def query_crossref_by_title(title, max_results=5):
    if not title or title == "Unknown":
        return []
    try:
        params = {"query.title": title, "rows": max_results}
        r = requests.get(CROSSREF_BASE, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("message", {}).get("items", [])
        return []
    except requests.exceptions.RequestException:
        return []


def extract_crossref_metadata(record):
    authors = record.get("author", [])
    if authors:
        full_author_list = ", ".join([f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors])
        first_author = f"{authors[0].get('given', '')} {authors[0].get('family', '')}".strip()
    else:
        full_author_list = "Unknown"
        first_author = "Unknown"

    title_list = record.get("title", [])
    title = title_list[0] if title_list else "Unknown"

    year = "Unknown"
    published = record.get("published-print") or record.get("published-online") or record.get("created")
    if published and "date-parts" in published:
        date_parts = published["date-parts"][0]
        if date_parts:
            year = date_parts[0]

    container_title = record.get("container-title", [])
    journal = container_title[0] if container_title else "Unknown"

    volume = record.get("volume", "-")
    issue = record.get("issue", "-")
    page = record.get("page", "-")

    doi = record.get("DOI", None)

    doc_type_raw = record.get("type", "unknown")
    doc_type_map = {
        "journal-article": "Journal Article",
        "book-chapter": "Book Chapter",
        "proceedings-article": "Conference Paper",
        "posted-content": "Preprint",
        "dataset": "Dataset",
        "book": "Book",
        "dissertation": "Dissertation",
        "unknown": "Unknown"
    }
    doc_type = doc_type_map.get(doc_type_raw, doc_type_raw.replace("-", " ").title())

    return {
        "oa_full_author": full_author_list,
        "oa_first_author": first_author,
        "oa_title": title,
        "oa_year": year,
        "oa_journal": journal,
        "oa_volume": volume,
        "oa_issue": issue,
        "oa_page_range": normalize_page_range(page),
        "openalex_id": "N/A (Crossref)",
        "oa_doi": doi,
        "is_retracted": False,
        "doc_type": doc_type,
        "data_source": "Crossref"
    }


def compare_metadata(parsed_ref, oa_meta):
    diff = {}

    ref_title_std = standardize_title(parsed_ref['ref_title'])
    oa_title_std = standardize_title(oa_meta['oa_title'])
    diff['oa_title'] = oa_meta['oa_title']
    diff['oa_title_diff'] = ref_title_std != oa_title_std

    ref_surname = extract_surname(parsed_ref['ref_first_author'])
    oa_surname = extract_surname(oa_meta['oa_first_author'])
    diff['oa_full_author'] = oa_meta['oa_full_author']
    diff['oa_full_author_diff'] = ref_surname != oa_surname

    ref_year = parsed_ref['ref_year']
    oa_year = oa_meta['oa_year']
    diff['oa_year'] = oa_year
    if ref_year and oa_year:
        delta = abs(ref_year - oa_year)
        diff['oa_year_delta'] = delta
        if delta == 0:
            diff['oa_year_diff'] = False
        elif delta <= 2:
            diff['oa_year_diff'] = "minor"
        else:
            diff['oa_year_diff'] = True
    else:
        diff['oa_year_diff'] = True
        diff['oa_year_delta'] = None

    for key in ['journal', 'volume', 'issue', 'page_range']:
        ref_val = str(parsed_ref[f'ref_{key}'])
        oa_val = str(oa_meta[f'oa_{key}'])

        if key == 'page_range':
            ref_val = normalize_page_range(ref_val)
            oa_val = normalize_page_range(oa_val)

        diff[f'oa_{key}'] = oa_val
        diff[f'oa_{key}_diff'] = ref_val != oa_val

    return diff


def verify_status(parsed_ref, oa_meta):
    score = 0

    title_score = fuzz.token_sort_ratio(
        standardize_title(parsed_ref['ref_title']),
        standardize_title(oa_meta['oa_title'])
    )
    if title_score >= 90:
        score += 2
    elif title_score >= 80:
        score += 1

    ref_surname = extract_surname(parsed_ref['ref_first_author'])
    oa_surname = extract_surname(oa_meta['oa_first_author'])
    if ref_surname and oa_surname and ref_surname == oa_surname:
        score += 1

    ref_year = parsed_ref['ref_year']
    oa_year = oa_meta['oa_year']
    if ref_year is not None and oa_year is not None and abs(ref_year - oa_year) <= 2:
        score += 1

    if score >= 4:
        return "verified", "high"
    elif score >= 2:
        return "ambiguous", "medium"
    else:
        return "unverified", "low"


def process_references(raw_references, progress_bar, status_text):
    results = []
    total = len(raw_references)

    for idx, raw in enumerate(raw_references):
        progress_bar.progress((idx + 1) / total)
        status_text.text(f"Processing {idx + 1}/{total}...")

        parsed = parse_reference(raw)

        oa_record_from_doi = query_openalex_by_doi(parsed['doi'])
        doi_lookup_success = bool(oa_record_from_doi)
        data_source = None

        if oa_record_from_doi:
            data_source = "OpenAlex"
        elif parsed['doi']:
            crossref_record = query_crossref_by_doi(parsed['doi'])
            if crossref_record:
                oa_record_from_doi = crossref_record
                doi_lookup_success = True
                data_source = "Crossref"

        time.sleep(REQUEST_DELAY)

        title_similarity_score = 0
        if oa_record_from_doi:
            if data_source == "OpenAlex":
                oa_title_from_doi = oa_record_from_doi.get("title", "")
            else:
                title_list = oa_record_from_doi.get("title", [])
                oa_title_from_doi = title_list[0] if title_list else ""

            title_similarity_score = fuzz.token_sort_ratio(
                standardize_title(parsed["ref_title"]),
                standardize_title(oa_title_from_doi)
            )

        oa_record = oa_record_from_doi
        matched_by_title = False

        if not oa_record or title_similarity_score < TITLE_MISMATCH_THRESHOLD:
            if parsed["ref_title"] != "Unknown":
                candidates = query_openalex_by_title(parsed["ref_title"])
                time.sleep(REQUEST_DELAY)

                if not candidates:
                    candidates = query_crossref_by_title(parsed["ref_title"])
                    data_source = "Crossref" if candidates else None
                    time.sleep(REQUEST_DELAY)
                else:
                    data_source = "OpenAlex"

                if candidates:
                    best_score = 0
                    best_record = None

                    for c in candidates:
                        if data_source == "OpenAlex":
                            c_title = c.get("title", "")
                        else:
                            c_title_list = c.get("title", [])
                            c_title = c_title_list[0] if c_title_list else ""

                        score = fuzz.token_sort_ratio(
                            standardize_title(parsed["ref_title"]),
                            standardize_title(c_title)
                        )
                        if score > best_score:
                            best_score = score
                            best_record = c

                    if best_score >= TITLE_THRESHOLD and best_record:
                        oa_record = best_record
                        matched_by_title = True

        if oa_record:
            if data_source == "OpenAlex":
                oa_meta = extract_openalex_metadata(oa_record)
            else:
                oa_meta = extract_crossref_metadata(oa_record)

            meta_diff = compare_metadata(parsed, oa_meta)
            status, confidence = verify_status(parsed, oa_meta)

            original_doi = parsed.get("doi")
            oa_doi = oa_meta.get("oa_doi")

            original_doi_norm = normalize_doi(original_doi)
            oa_doi_norm = normalize_doi(oa_doi)

            final_title_similarity = fuzz.token_sort_ratio(
                standardize_title(parsed["ref_title"]),
                standardize_title(oa_meta["oa_title"])
            )

            if original_doi:
                if doi_lookup_success and final_title_similarity < TITLE_MISMATCH_THRESHOLD:
                    filled_doi = None
                    doi_fill_status = "doi_title_mismatch"
                elif oa_doi_norm and original_doi_norm == oa_doi_norm and final_title_similarity >= TITLE_MISMATCH_THRESHOLD:
                    filled_doi = original_doi
                    doi_fill_status = "original_correct"
                elif matched_by_title and oa_doi:
                    filled_doi = oa_doi
                    doi_fill_status = "title_matched_doi_corrected"
                elif oa_doi and original_doi_norm != oa_doi_norm:
                    filled_doi = oa_doi
                    doi_fill_status = "original_wrong_corrected"
                else:
                    filled_doi = original_doi
                    doi_fill_status = "original_unverified"
            elif oa_doi:
                filled_doi = oa_doi
                doi_fill_status = "filled_from_database"
            else:
                filled_doi = None
                doi_fill_status = "missing"

            is_retracted = oa_meta.get('is_retracted', False)
        else:
            oa_meta = {
                k: "Unknown" for k in [
                    "oa_title", "oa_first_author", "oa_year", "oa_journal",
                    "oa_volume", "oa_issue", "oa_page_range", "openalex_id", "oa_doi"
                ]
            }
            oa_meta['is_retracted'] = False
            oa_meta['data_source'] = "None"
            oa_meta['doc_type'] = "Unknown"
            meta_diff = {
                f"{k}_diff": False for k in [
                    "oa_title", "oa_first_author", "oa_year", "oa_journal",
                    "oa_volume", "oa_issue", "oa_page_range", "openalex_id", "oa_doi"
                ]
            }
            meta_diff['oa_year_diff'] = True
            meta_diff['oa_year_delta'] = None
            status, confidence = "unverified", "unverified"
            is_retracted = False

            original_doi = parsed.get("doi")
            if original_doi:
                filled_doi = None
                doi_fill_status = "unverified"
            else:
                filled_doi = None
                doi_fill_status = "missing"

        result = {
            **parsed,
            **oa_meta,
            **meta_diff,
            "filled_doi": filled_doi,
            "doi_fill_status": doi_fill_status,
            "status": status,
            "confidence": confidence
        }
        results.append(result)

    return results


# ================= Streamlit UI =================

def main():
    st.set_page_config(
        page_title="Reference Verification Tool",
        page_icon="📚",
        layout="wide"
    )

    st.title("📚 Reference Verification Tool")
    st.markdown("**Supports:** APA, Chicago, Harvard, IEEE, ACM, MLA")

    st.markdown("---")

    # Sidebar
    with st.sidebar:
        st.header("ℹ️ Instructions")
        st.markdown("""
        1. **Paste references** (one per line) in the text box
        2. Click **Run Verification**
        3. View results and download CSV

        **Data Sources:**
        - 🔵 OpenAlex (primary)
        - 🟠 Crossref (fallback)
        """)

        st.markdown("---")
        st.markdown("**Status Legend:**")
        st.markdown("✅ Verified | ⚠️ Ambiguous | ❌ Unverified")

    # Main input
    st.subheader("📝 Input References")
    st.info("⚠️ **ONE REFERENCE PER LINE**")

    references_text = st.text_area(
        "Paste your references here:",
        height=200,
        placeholder="Example:\nCortes, C., & Vapnik, V. (1995). Support-vector Networks. Machine Learning, 20(3), 273–297.\nHinton, G. E., Osindero, S., & Teh, Y. (2006). A fast learning algorithm for deep belief nets. Neural Computation, 18(7), 1527–1554. https://doi.org/10.1162/neco.2006.18.7.1527."
    )

    if st.button("🚀 Run Verification", type="primary"):
        if not references_text.strip():
            st.error("❌ Please enter at least one reference.")
            return

        references = [line.strip() for line in references_text.split('\n') if line.strip()]

        st.markdown(f"**Total references:** {len(references)}")

        # Progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()

        # Process references
        results = process_references(references, progress_bar, status_text)

        progress_bar.empty()
        status_text.empty()

        # Create DataFrame
        df = pd.DataFrame(results)

        status_icon_map = {"verified": "✅", "ambiguous": "⚠️", "unverified": "❌"}
        df['status_icon'] = df['status'].map(status_icon_map)

        # Reorder columns
        front_cols = ["status", "confidence", "status_icon", "is_retracted", "doc_type", "data_source"]
        doi_cols = ["doi", "filled_doi", "doi_fill_status"]
        other_cols = [c for c in df.columns if
                      c not in front_cols + doi_cols + ["oa_first_author_diff", "openalex_id_diff"]]
        df = df[front_cols + doi_cols + other_cols]

        # Statistics
        st.markdown("---")
        st.subheader("📊 Verification Statistics")

        col1, col2, col3, col4 = st.columns(4)

        verified_count = len(df[df['status'] == 'verified'])
        ambiguous_count = len(df[df['status'] == 'ambiguous'])
        unverified_count = len(df[df['status'] == 'unverified'])
        retracted_count = len(df[df['is_retracted'] == True])

        col1.metric("✅ Verified", f"{verified_count} ({verified_count / len(df) * 100:.1f}%)")
        col2.metric("⚠️ Ambiguous", f"{ambiguous_count} ({ambiguous_count / len(df) * 100:.1f}%)")
        col3.metric("❌ Unverified", f"{unverified_count} ({unverified_count / len(df) * 100:.1f}%)")
        col4.metric("🚨 Retracted", retracted_count)

        # Document types
        st.markdown("### 📄 Document Types")
        col1, col2, col3 = st.columns(3)

        journal_count = len(df[df['doc_type'] == 'Journal Article'])
        conf_count = len(df[df['doc_type'] == 'Conference Paper'])
        book_count = len(df[df['doc_type'] == 'Book Chapter'])

        col1.metric("📘 Journal Articles", journal_count)
        col2.metric("📙 Conference Papers", conf_count)
        col3.metric("📕 Book Chapters", book_count)

        # Data sources
        st.markdown("### 🗄️ Data Sources")
        col1, col2, col3 = st.columns(3)

        openalex_count = len(df[df['data_source'] == 'OpenAlex'])
        crossref_count = len(df[df['data_source'] == 'Crossref'])
        none_count = len(df[df['data_source'] == 'None'])

        col1.metric("📘 OpenAlex", openalex_count)
        col2.metric("📙 Crossref", crossref_count)
        col3.metric("❌ Not Found", none_count)

        # DOI status
        st.markdown("### 📋 DOI Status")
        col1, col2, col3, col4 = st.columns(4)

        doi_correct = len(df[df['doi_fill_status'] == 'original_correct'])
        doi_filled = len(df[df['doi_fill_status'] == 'filled_from_database'])
        doi_corrected = len(df[df['doi_fill_status'] == 'original_wrong_corrected'])
        doi_missing = len(df[df['doi_fill_status'] == 'missing'])

        col1.metric("✓ Correct", doi_correct)
        col2.metric("➕ Filled", doi_filled)
        col3.metric("🔧 Corrected", doi_corrected)
        col4.metric("❓ Missing", doi_missing)

        # Display results
        st.markdown("---")
        st.subheader("📋 Detailed Results")

        # Color mapping function
        def color_cells(val, column_name, row):
            """Apply colors based on verification status and differences"""

            # Document Type colors
            if column_name == "doc_type":
                if val == "Journal Article":
                    return 'background-color: #E3F2FD; color: #1976D2; font-weight: bold'
                elif val == "Conference Paper":
                    return 'background-color: #FFF3E0; color: #FF6F00; font-weight: bold'
                elif val == "Book Chapter":
                    return 'background-color: #F3E5F5; color: #7B1FA2; font-weight: bold'
                elif val == "Preprint":
                    return 'background-color: #E0F2F1; color: #00897B; font-weight: bold'

            # Data Source colors
            if column_name == "data_source":
                if val == "OpenAlex":
                    return 'background-color: #E3F2FD; color: #1976D2; font-weight: bold'
                elif val == "Crossref":
                    return 'background-color: #FFF3E0; color: #FF6F00; font-weight: bold'
                elif val == "None":
                    return 'background-color: #F5F5F5; color: gray'

            # Retraction warning
            if column_name == "is_retracted":
                if val == True:
                    return 'background-color: #D32F2F; color: white; font-weight: bold'
                else:
                    return 'background-color: #E8F5E9; color: #2E7D32'

            # DOI Fill Status colors
            if column_name == "doi_fill_status":
                if val == "original_correct":
                    return 'background-color: #E8F5E9; color: #2E7D32; font-weight: bold'
                elif val == "filled_from_database":
                    return 'background-color: #E3F2FD; color: #1976D2; font-weight: bold'
                elif val == "title_matched_doi_corrected":
                    return 'background-color: #B3E5FC; color: #01579B; font-weight: bold'
                elif val == "original_wrong_corrected":
                    return 'background-color: #FFA726; color: white; font-weight: bold'
                elif val == "doi_title_mismatch":
                    return 'background-color: #E91E63; color: white; font-weight: bold'
                elif val == "unverified":
                    return 'background-color: #FFCDD2; color: #C62828; font-weight: bold'
                elif val == "missing":
                    return 'background-color: #F5F5F5; color: gray'

            # Filled DOI colors
            if column_name == "filled_doi":
                status = row['doi_fill_status']
                if status == "filled_from_database":
                    return 'background-color: #E3F2FD; color: #1976D2; font-weight: bold'
                elif status == "title_matched_doi_corrected":
                    return 'background-color: #B3E5FC; color: #01579B; font-weight: bold'
                elif status == "original_wrong_corrected":
                    return 'background-color: #FFE0B2; color: #E65100; font-weight: bold'
                elif status in ["unverified", "doi_title_mismatch"]:
                    return 'background-color: #FFCDD2; color: #C62828; font-weight: bold'

            # Original DOI colors
            if column_name == "doi":
                status = row['doi_fill_status']
                if status in ["unverified", "doi_title_mismatch"]:
                    return 'background-color: #FFCDD2; color: #C62828; font-weight: bold'

            # Year difference colors
            if column_name == "oa_year":
                if row['oa_year_diff'] == False:
                    return 'background-color: #E8F5E9; color: #2E7D32'
                elif row['oa_year_diff'] == "minor":
                    return 'background-color: #FFF9C4; color: #F57F17; font-weight: bold'
                elif row['oa_year_diff'] == True:
                    return 'background-color: #FFCDD2; color: #C62828'

            # Highlight differences in OpenAlex data
            if column_name in ["oa_title", "oa_full_author", "oa_journal", "oa_volume", "oa_issue", "oa_page_range"]:
                diff_col = f"{column_name}_diff"
                if diff_col in row and row[diff_col] == True:
                    return 'background-color: #FFCDD2; color: #C62828'
                elif row['status'] == 'verified':
                    return 'background-color: #E8F5E9; color: #2E7D32'
                elif row['status'] == 'unverified':
                    return 'background-color: #FFEBEE; color: #C62828'

            # Status colors
            if column_name == "status":
                if val == "verified":
                    return 'background-color: #E8F5E9; color: #2E7D32; font-weight: bold'
                elif val == "ambiguous":
                    return 'background-color: #FFF9C4; color: #F57F17; font-weight: bold'
                elif val == "unverified":
                    return 'background-color: #FFCDD2; color: #C62828; font-weight: bold'

            # Confidence colors
            if column_name == "confidence":
                if val == "high":
                    return 'background-color: #E8F5E9; color: #2E7D32'
                elif val == "medium":
                    return 'background-color: #FFF9C4; color: #F57F17'
                elif val == "low" or val == "unverified":
                    return 'background-color: #FFCDD2; color: #C62828'

            return ''

        # Apply styling with color function
        def apply_row_styles(row):
            return [color_cells(val, col, row) for col, val in row.items()]

        styled_df = df.style.apply(apply_row_styles, axis=1)

        # Display with custom CSS
        st.markdown("""
        <style>
        /* Make table more readable */
        .dataframe {
            font-size: 11px;
        }
        .dataframe td {
            padding: 8px !important;
            border: 1px solid #ddd !important;
        }
        .dataframe th {
            background-color: #1976D2 !important;
            color: white !important;
            font-weight: bold !important;
            padding: 10px !important;
            text-align: left !important;
        }
        </style>
        """, unsafe_allow_html=True)

        st.dataframe(styled_df, use_container_width=True, height=500)

        # Download button
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="💾 Download Results (CSV)",
            data=csv,
            file_name="verification_results.csv",
            mime="text/csv",
        )

        st.success("✅ Processing complete!")

if __name__ == "__main__":
    main()
