import json
from pathlib import Path

from django.conf import settings

from app.models import Course, Tuition


ROOT = Path(settings.BASE_DIR)

ROUTES = {
    "C_UK001_31": {
        "path": "accounting-and-finance-with-business-analytics",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_33": {
        "path": "accounting-and-finance-with-people-management",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_32": {
        "path": "accounting-and-finance-with-law",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_04": {
        "path": "business-management-with-analytics",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_34": {
        "path": "business-management-with-events-management",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_35": {
        "path": "business-management-with-people-management",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_36": {
        "path": "digital-marketing-and-business-management",
        "entry_year": 3,
        "duration": "2 years (full-time)",
    },
    "C_UK001_40": {
        "path": "environmental-management-sustainability",
        "entry_year": 2,
        "duration": "3 years (full-time)",
    },
}


def normalize_title(title):
    return " ".join(
        title.casefold().replace("&", " and ").split()
    )


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

    for course_id, route in ROUTES.items():
        source_url = (
            "https://www.abertay.ac.uk/course-search/"
            f'undergraduate/{route["path"]}/'
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
                "reason": "Expected one extracted source record.",
            })
            continue

        record = records[0]
        data = record["data"]

        if (
            data.get("duration") != route["duration"]
            or data.get("academic_year") != "2026/27"
            or f'Yr{route["entry_year"]}'
            not in record.get("catalogue_title", "")
        ):
            blocked.append({
                "course_id": course_id,
                "reason": "Source entry route or duration differs.",
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

        # Preserve degree distinctions while tolerating & versus 'and'.
        if normalize_title(course.title) != normalize_title(data["title"]):
            blocked.append({
                "course_id": course_id,
                "reason": "Stored and source titles differ.",
            })
            continue

        if course.duration != "4 Years":
            blocked.append({
                "course_id": course_id,
                "reason": (
                    "Stored duration is not the expected original "
                    "'4 Years'; inspect before correcting."
                ),
            })
            continue

        tuitions = list(
            Tuition.objects.filter(course=course).order_by("pk")
        )

        proposals.append({
            "course_id": course_id,
            "source_url": source_url,
            "catalogue_title": record["catalogue_title"],
            "source_award": data.get("award_title"),
            "ucas_code": data.get("ucas_code"),
            "entry_year": route["entry_year"],
            "before": {
                "title": course.title,
                "duration": course.duration,
                "course_url": course.course_url,
                "ucas_cao_code": course.ucas_cao_code,
            },
            "proposed": {
                "title": data["title"],
                "duration": data["duration"],
                "course_url": source_url,
                "ucas_cao_code": data.get("ucas_code"),
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
        })

    report = {
        "database_changes": False,
        "academic_year": "2026/27",
        "proposals": proposals,
        "blocked": blocked,
    }

    output = ROOT / 'data/universities/uk/abertay/abertay_entry_routes_preview.json'
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