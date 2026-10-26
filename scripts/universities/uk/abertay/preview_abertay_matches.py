

# Locate the project root after this script has been relocated.
import sys as _script_sys
from pathlib import Path as _ScriptPath

_script_locations = []
if "__file__" in globals():
    _script_locations.extend(_ScriptPath(__file__).resolve().parents)
_script_locations.extend([
    _ScriptPath.cwd(),
    *_ScriptPath.cwd().parents,
])

_SCRIPT_ROOT = next(
    (
        location for location in _script_locations
        if (location / "manage.py").is_file()
        and (location / "app").is_dir()
    ),
    None,
)
if _SCRIPT_ROOT is None:
    raise RuntimeError(
        "Cannot locate the Academy project. Run from its main folder."
    )

if str(_SCRIPT_ROOT) not in _script_sys.path:
    _script_sys.path.insert(0, str(_SCRIPT_ROOT))

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

#Our next step is a matching preview, with no database changes

ROOT = _SCRIPT_ROOT

AWARD_PATTERN = re.compile(
    r"\b(?:BSc|BA|BEng|MEng|LLB|MSc|MA|MRes)\b",
    re.IGNORECASE,
)


def normalize_subject(title):
    """Normalize wording for candidate discovery, not identity approval."""
    text = unicodedata.normalize("NFKC", title).casefold()
    text = text.replace("&", " and ")

    # Standardize the descriptive law qualification.
    text = re.sub(r"\bbachelor of laws\b", "law", text)

    # Compare award types separately.
    text = AWARD_PATTERN.sub(" ", text)
    text = re.sub(r"\b(?:hons|honours)\b", " ", text)

    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def award_types(text):
    return sorted({
        match.upper()
        for match in AWARD_PATTERN.findall(text)
    })


def simple_duration(text):
    """Return years only when the text contains one clear year value."""
    years = re.findall(
        r"\b(\d+(?:\.\d+)?)\s*years?\b",
        text,
        flags=re.IGNORECASE,
    )
    return years[0] if len(years) == 1 else None


def inspect_candidate(source, existing, subject_counts):
    data = source["data"]
    flags = []

    source_awards = award_types(
        data.get("award_title") or data["title"]
    )
    stored_awards = award_types(existing["title"])

    if not source_awards or not stored_awards:
        flags.append("award_not_confirmed")
    elif source_awards != stored_awards:
        flags.append("award_mismatch")

    if len(source_awards) > 1:
        flags.append("multiple_source_awards")

    source_years = simple_duration(data.get("duration", ""))
    stored_years = simple_duration(existing.get("duration", ""))

    if source_years is None or stored_years is None:
        flags.append("duration_requires_review")
    elif source_years != stored_years:
        flags.append("duration_mismatch")

    catalogue_title = source.get("catalogue_title", "")

    if re.search(r"\bYr\s*[234]\b", catalogue_title, re.IGNORECASE):
        flags.append("advanced_entry_route")

    if existing.get("level") == "TOPUP":
        flags.append("topup_identity_requires_review")

    subject = normalize_subject(data["title"])
    if subject_counts[subject] > 1:
        flags.append("multiple_source_routes")

    # All supplied database URLs currently point to the homepage.
    stored_url = existing.get("course_url", "").strip().rstrip("/")
    stored_url = re.sub(r"^https?://", "", stored_url)

    if stored_url.casefold() in {
        "abertay.ac.uk",
        "www.abertay.ac.uk",
    }:
        flags.append("stored_url_is_homepage")

    return {
        "course_id": existing["course_id"],
        "stored_title": existing["title"],
        "stored_level": existing["level"],
        "stored_duration": existing.get("duration", ""),
        "flags": flags,
    }


def main():
    database_courses = json.loads(
        (ROOT / 'data/universities/uk/abertay/abertay_database_courses.json').read_text(
            encoding="utf-8-sig"
        )
    )
    preview = json.loads(
        (ROOT / 'data/universities/uk/abertay/abertay_courses_preview.json').read_text(
            encoding="utf-8-sig"
        )
    )

    extracted = [
        record
        for record in preview["records"]
        if record.get("status") == "extracted"
    ]

    subject_counts = Counter(
        normalize_subject(record["data"]["title"])
        for record in extracted
    )

    reports = []
    candidate_ids = set()

    for source in extracted:
        subject = normalize_subject(source["data"]["title"])

        candidates = [
            inspect_candidate(source, existing, subject_counts)
            for existing in database_courses
            if normalize_subject(existing["title"]) == subject
        ]

        candidate_ids.update(
            candidate["course_id"] for candidate in candidates
        )

        reports.append({
            "source_title": source["data"]["title"],
            "catalogue_title": source.get("catalogue_title", ""),
            "source_url": source["source_url"],
            "source_award": source["data"].get("award_title", ""),
            "source_duration": source["data"].get("duration", ""),
            "annual_tuition": source["data"]["annual_tuition"],
            "academic_year": source["data"]["academic_year"],
            "status": "needs_review" if candidates else "unmatched",
            "candidates": candidates,
        })

    output = {
        "database_changes": False,
        "source_courses": reports,
        "extraction_needs_review": [
            record
            for record in preview["records"]
            if record.get("status") != "extracted"
        ],
        "database_courses_without_candidates": [
            course
            for course in database_courses
            if course["course_id"] not in candidate_ids
        ],
    }

    output_path = ROOT / 'data/universities/uk/abertay/abertay_matches_preview.json'
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    matched = sum(bool(row["candidates"]) for row in reports)

    print(f"Extracted courses checked: {len(reports)}")
    print(f"Courses with review candidates: {matched}")
    print(f"Courses without candidates: {len(reports) - matched}")
    print(f"Preview saved to: {output_path}")
    print("No database records changed.")


if __name__ == "__main__":
    main()