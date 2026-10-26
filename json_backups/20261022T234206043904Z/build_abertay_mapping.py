import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent

def main():
    report = json.loads(
        (ROOT / "abertay_matches_preview.json").read_text(
            encoding="utf-8"
        )
    )

    mappings = []

    for source in report["source_courses"]:
        candidates =  source["candidates"]

        # Require exactly one database candidate.
        if  len(candidates) != 1:
            continue
        candidate = candidates[0]
        # Exclude award, duration and entry-route conflicts.
        if set(candidate["flags"]) != {"stored_url_is_homepage"}:
            continue

        mappings.append({
            "course_id": candidate["course_id"],
            "source_url": source["source_url"],
            "expected_stored_title": candidate["stored_title"],
            "expected_stored_duration": candidate["stored_duration"],
            "expected_stored_level": candidate["stored_level"],
            "source_title": source["source_title"],
            "source_award": source["source_award"],
            "source_duration": source["source_duration"],
            "approved": False,
        })

        # Prevent two source pages from targeting the same database record.
    id_counts = Counter(row["course_id"] for row in mappings)
    url_counts = Counter(row["source_url"] for row in mappings)

    if any(count > 1 for count in id_counts.values()):
        raise ValueError("Duplicate course IDs found in mapping.")
    if any(count > 1 for count in url_counts.values()):
        raise ValueError("Duplicate source URLs found in mapping.")

    output = {
        "university_name": "Abertay University",
        "academic_year": "2026/27",
        "fee_status": "International",
        "currency": "GBP",
        "mappings": mappings,
    }
    output_path = ROOT / "abertay_course_mapping.json"
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"Mapping draft contains {len(mappings)} courses.")
    print(f"Saved to: {output_path}")
    print("No database records changed.")

if __name__ == "__main__":
    main()

