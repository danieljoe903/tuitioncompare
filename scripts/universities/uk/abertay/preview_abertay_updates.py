

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
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from app.models import Course, Tuition


def main():
    root = Path(settings.BASE_DIR)

    mapping = json.loads(
        (root / 'data/universities/uk/abertay/abertay_course_mapping.json').read_text(
            encoding="utf-8-sig"
        )
    )
    extraction = json.loads(
        (root / 'data/universities/uk/abertay/abertay_courses_preview.json').read_text(
            encoding="utf-8-sig"
        )
    )

    # Keep lists so duplicate source URLs cannot silently overwrite data.
    sources = {}
    for record in extraction["records"]:
        if record.get("status") == "extracted":
            sources.setdefault(record["source_url"], []).append(record)

    updates = []
    blocked = []

    for entry in mapping["mappings"]:
        course_id = entry["course_id"]

        def block(reason):
            blocked.append({
                "course_id": course_id,
                "reason": reason,
            })

        records = sources.get(entry["source_url"], [])
        if len(records) != 1:
            block("Expected exactly one extracted source record.")
            continue

        data = records[0]["data"]

        # Confirm that the extracted data still matches the mapping.
        expected_source = {
            "title": entry["source_title"],
            "award_title": entry["source_award"],
            "duration": entry["source_duration"],
            "academic_year": mapping["academic_year"],
            "fee_status": mapping["fee_status"],
            "currency": mapping["currency"],
        }

        if any(data.get(key) != value
               for key, value in expected_source.items()):
            block("Extracted source differs from the mapping.")
            continue

        courses = list(
            Course.objects.filter(
                course_id=course_id,
                university__name__iexact=mapping["university_name"],
            )[:2]
        )

        if len(courses) != 1:
            block("Expected exactly one matching database course.")
            continue

        course = courses[0]

        expected_course = {
            "title": entry["expected_stored_title"],
            "duration": entry["expected_stored_duration"],
            "level": entry["expected_stored_level"],
        }

        if any(getattr(course, key) != value
               for key, value in expected_course.items()):
            block("Database course changed since the mapping was built.")
            continue

        if course.start_year != int(mapping["academic_year"][:4]):
            block("Course intake year differs from the mapped academic year.")
            continue

        tuitions = list(
            Tuition.objects.filter(
                course=course,
                residency="INTL",
            )[:2]
        )

        if len(tuitions) != 1:
            block("Expected exactly one international tuition record.")
            continue

        tuition = tuitions[0]

        if tuition.currency != data["currency"]:
            block("Database and source currencies differ.")
            continue

        proposed_fee = Decimal(data["annual_tuition"])
        if not proposed_fee.is_finite() or proposed_fee <= 0:
            block("Source tuition must be a positive, finite amount.")
            continue

        existing_fee = Decimal(str(tuition.tuition_fee))

        updates.append({
            "course_id": course_id,
            "title": course.title,
            "tuition_id": tuition.pk,
            "academic_year": mapping["academic_year"],
            "currency": tuition.currency,
            "current_tuition": str(existing_fee),
            "proposed_tuition": str(proposed_fee),
            "difference": str(proposed_fee - existing_fee),
            "current_course_url": course.course_url,
            "proposed_course_url": entry["source_url"],
            "mapping_approved": entry.get("approved") is True,
            "scholarship_action": "Preserve existing amount and links",
        })

    report = {
        "database_changes": False,
        "academic_year": mapping["academic_year"],
        "updates": updates,
        "blocked": blocked,
    }

    output_path = root / 'data/universities/uk/abertay/abertay_updates_preview.json'
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for row in updates:
        print(
            f'{row["course_id"]}: '
            f'{row["currency"]} {row["current_tuition"]} '
            f'-> {row["proposed_tuition"]}'
        )

    print(f"\nPreviewed: {len(updates)}")
    print(f"Blocked: {len(blocked)}")
    print(f"Saved to: {output_path}")
    print("No database records changed.")


main()