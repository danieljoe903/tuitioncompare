

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
from pathlib import Path

from django.conf import settings

from app.models import Course, Tuition


ROOT = Path(settings.BASE_DIR)

# Explicit corrections supported by the extracted course pages.
CORRECTIONS = {
    "C_UK001_10": {
        "path": "mental-health-nursing",
        "expected_title": "BSc (Hons) Mental Health Nursing",
        "expected_duration": "4 Years",
        "source_title": "BSc (Hons) Mental Health Nursing",
        "source_award": "BSc (Hons)",
        "source_duration": "3 years (full-time)",
    },
    "C_UK001_79": {
        "path": "game-design-and-production",
        "expected_title": "BSc (Hons) Game Design and Production",
        "expected_duration": "4 Years",
        "source_title": "BA (Hons) Game Design and Production",
        "source_award": "BA (Hons)",
        "source_duration": "4 years (full-time)",
    },
    "C_UK001_44": {
        "path": "sport-and-management",
        "expected_title": "BSc (Hons) Sport and Management",
        "expected_duration": "4 Years",
        "source_title": "BA (Hons) Sport & Management",
        "source_award": "BA (Hons)",
        "source_duration": "4 years (full-time)",
    },
}


def main():
    extraction = json.loads(
        (ROOT / 'data/universities/uk/abertay/abertay_courses_preview.json').read_text(
            encoding="utf-8-sig"
        )
    )

    if extraction["academic_year"] != "2026/27":
        raise ValueError("Expected extraction for 2026/27.")

    proposals = []
    blocked = []

    for course_id, correction in CORRECTIONS.items():
        source_url = (
            "https://www.abertay.ac.uk/course-search/"
            f'undergraduate/{correction["path"]}/'
        )

        records = [
            record
            for record in extraction["records"]
            if record.get("status") == "extracted"
            and record["source_url"] == source_url
        ]

        if len(records) != 1:
            blocked.append({
                "course_id": course_id,
                "reason": "Expected one extracted course page.",
            })
            continue

        data = records[0]["data"]

        expected_source = {
            "title": correction["source_title"],
            "award_title": correction["source_award"],
            "duration": correction["source_duration"],
            "academic_year": "2026/27",
            "fee_status": "International",
            "currency": "GBP",
        }

        if any(
            data.get(key) != value
            for key, value in expected_source.items()
        ):
            blocked.append({
                "course_id": course_id,
                "reason": "Source differs from the reviewed correction.",
            })
            continue

        courses = list(
            Course.objects.filter(
                course_id=course_id,
                university__name__iexact="Abertay University",
                university__country="UK",
                level="UG",
                start_year=2026,
            )[:2]
        )

        if len(courses) != 1:
            blocked.append({
                "course_id": course_id,
                "reason": "Expected one matching database course.",
            })
            continue

        course = courses[0]

        if (
            course.title != correction["expected_title"]
            or course.duration != correction["expected_duration"]
        ):
            blocked.append({
                "course_id": course_id,
                "reason": (
                    "Stored title or duration differs from "
                    "the expected original record."
                ),
            })
            continue

        # A course correction may affect every residency's tuition
        # and scholarship eligibility, not only the INTL record.
        tuitions = list(
            Tuition.objects.filter(course=course).order_by("pk")
        )

        proposal = {
            "course_id": course_id,
            "source_url": source_url,
            "ucas_code": data.get("ucas_code"),
            "before": {
                "title": course.title,
                "duration": course.duration,
                "course_url": course.course_url,
            },
            "proposed": {
                "title": data["title"],
                "duration": data["duration"],
                "course_url": source_url,
            },
            "related_tuitions": [
                {
                    "tuition_id": tuition.pk,
                    "residency": tuition.residency,
                    "tuition_fee": str(tuition.tuition_fee),
                    "scholarship": str(tuition.scholarship),
                    "scholarship_source_id":
                        tuition.scholarship_source_id,
                    "currency": tuition.currency,
                }
                for tuition in tuitions
            ],
            "requires_scholarship_review": any(
                tuition.scholarship > 0
                or tuition.scholarship_source_id is not None
                for tuition in tuitions
            ),
            "approved": False,
        }

        proposals.append(proposal)

    report = {
        "database_changes": False,
        "academic_year": "2026/27",
        "proposals": proposals,
        "blocked": blocked,
    }

    output = ROOT / 'data/universities/uk/abertay/abertay_corrections_preview.json'
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nProposed corrections: {len(proposals)}")
    print(f"Blocked: {len(blocked)}")
    print(f"Saved to: {output}")
    print("No database records changed.")


main()