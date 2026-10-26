"""Preview Arden campus postgraduate fees from saved evidence.

Run through Django's shell. No database records are changed.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup
from django.conf import settings

from app.models import Course, Tuition


ROOT = Path(settings.BASE_DIR)
BASE = ROOT / "logs" / "arden"

ROUTES = {
    "C_UK004_16": "master-of-business-administration",
    "C_UK004_17": "msc-data-science",
    "C_UK004_18": "msc-cyber-security",
    "C_UK004_19": "master-public-health",
    "C_UK004_20": "msc-project-management-technology",
    "C_UK004_21": "msc-international-business-management",
    "C_UK004_22": "msc-engineering-management",
    "C_UK004_23": "msc-strategic-digital-marketing",
    "C_UK004_24": "msc-strategic-human-resource-management",
    "C_UK004_25": "msc-supply-chain-management-and-logistics",
    "C_UK004_40": "executive-mba",
}

CAMPUSES = {
    "bl1680": "London Stratford",
    "bl1666": "Manchester",
}


def latest(pattern, required):
    folders = [
        path for path in BASE.glob(pattern)
        if path.is_dir() and (path / required).is_file()
    ]
    if not folders:
        raise ValueError(f"Missing evidence: {pattern}/{required}")
    return max(folders, key=lambda path: path.name)


def money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def run():
    course_folder = latest("course-evidence-*", "course-evidence.json")
    fee_folder = latest("dynamic-fees-*", "dynamic-fees.json")

    evidence = json.loads(
        (course_folder / "course-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    fees = json.loads(
        (fee_folder / "dynamic-fees.json").read_text(
            encoding="utf-8"
        )
    )

    if (
        evidence.get("university_id") != "UK004"
        or fees.get("university_id") != "UK004"
    ):
        raise ValueError("Expected Arden UK004 evidence.")

    if Path(fees["source_evidence"]).name != course_folder.name:
        raise ValueError("Fee responses belong to different course evidence.")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "preview",
        "database_changes": False,
        "mappings_approved": False,
        "course_evidence": str(course_folder),
        "fee_evidence": str(fee_folder),
        "proposals": [],
        "blocked": [],
    }

    for course_id, slug in ROUTES.items():
        try:
            course = Course.objects.get(
                university_id="UK004", course_id=course_id
            )
            if course.level != "PG":
                raise ValueError("Stored course is not postgraduate.")

            pages = [
                page for page in evidence["pages"]
                if page["source_url"].rstrip("/").endswith("/" + slug)
            ]
            if len(pages) != 1:
                raise ValueError("A unique collected course page is required.")
            page = pages[0]

            html = (
                course_folder / Path(page["html_file"]).name
            ).read_bytes()
            if hashlib.sha256(html).hexdigest() != page["sha256"]:
                raise ValueError("Course HTML checksum mismatch.")

            soup = BeautifulSoup(html, "html.parser")

            course_field = soup.find(
                "input", attrs={"name": "fees_course_id"}
            )
            if course_field is None:
                raise ValueError("Missing source course identifier.")
            source_id = course_field.get("value", "")

            # Read the campus/intake/full-time relationship explicitly.
            selector = soup.find(
                "select",
                attrs={"name": "apply_now_course_study_type"},
            )
            if selector is None:
                raise ValueError("No campus-specific study options found.")

            campus_intakes = {}
            for option in selector.find_all("option"):
                value = option.get("value", "")
                match = re.fullmatch(
                    r"c(\d+)\|t(bl1680|bl1666)\|i(\d{4}-\d{2})\|m=ft",
                    value,
                )
                if match and match.group(1) == source_id:
                    campus = CAMPUSES[match.group(2)]
                    campus_intakes.setdefault(campus, set()).add(
                        match.group(3)
                    )

            if not campus_intakes:
                raise ValueError("No named UK campus full-time intake found.")

            responses = [
                item for item in fees["responses"]
                if item["course_page"] == page["source_url"]
            ]

            country_responses = {}
            for country in ("Nigeria", "Ghana"):
                selected = [
                    item for item in responses
                    if item["country"] == country
                ]
                if len(selected) != 1:
                    raise ValueError(
                        f"Expected one fee response for {country}."
                    )
                response = selected[0]

                if response["request_parameters"]["course_id"] != source_id:
                    raise ValueError("Fee/source course ID mismatch.")

                raw = (
                    fee_folder / response["raw_response_file"]
                ).read_bytes()
                if (
                    hashlib.sha256(raw).hexdigest()
                    != response["response_sha256"]
                ):
                    raise ValueError("Fee response checksum mismatch.")

                payload = json.loads(raw)
                fragment = BeautifulSoup(
                    payload["data"], "html.parser"
                )
                text = fragment.get_text(" ", strip=True)

                if text != response["text"]:
                    raise ValueError("Fee text differs from raw response.")

                country_responses[country] = {
                    "text": text,
                    "source_url": response["fee_source_url"],
                }

            if (
                country_responses["Nigeria"]["text"]
                != country_responses["Ghana"]["text"]
            ):
                raise ValueError("Country fee responses differ.")

            text = country_responses["Nigeria"]["text"]
            campus_prices = {}

            for campus, marker in (
                ("Manchester", "Manchester Campus"),
                ("London Stratford", "London, Stratford Campus"),
            ):
                if campus not in campus_intakes:
                    continue

                pattern = (
                    re.escape(marker)
                    + r"\s+Fee per year:\s*£([\d,]+(?:\.\d{2})?)"
                )
                amounts = re.findall(pattern, text)

                if len(amounts) != 1:
                    raise ValueError(
                        f"{campus}: unique annual gross fee not found."
                    )

                campus_prices[campus] = {
                    "gross_annual_tuition": money(
                        amounts[0].replace(",", "")
                    ),
                    "advertised_full_time_intakes": sorted(
                        campus_intakes[campus]
                    ),
                }

            if not campus_prices:
                raise ValueError("No applicable campus price found.")

            tuitions = list(Tuition.objects.filter(
                course_id=course_id, residency="INTL"
            ))
            if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                raise ValueError(
                    "Expected one existing GBP international tuition."
                )

            proposal = {
                "course_id": course_id,
                "stored_title": course.title,
                "source_heading": page["heading"],
                "source_course_id": source_id,
                "source_course_url": page["source_url"],
                "before": {
                    "location": course.location,
                    "start_month": course.start_month,
                    "start_year": course.start_year,
                    "tuition_fee": money(tuitions[0].tuition_fee),
                },
                "campus_options": campus_prices,
                "currency": "GBP",
                "fee_basis": "annual_tuition",
                "countries_checked": country_responses,
                "academic_year": None,
                "review_notes": [
                    "Campus/intake options are preserved individually.",
                    "The fee response does not explicitly label an academic year.",
                    "A final campus/intake mapping is required before import.",
                    "Offer deductions require separate eligibility/terms verification.",
                    "No numeric deposit has been verified by this preview.",
                ],
            }
            report["proposals"].append(proposal)

            print(f"\n{course_id}: {course.title}")
            print(
                f"  Stored: GBP {money(tuitions[0].tuition_fee)}; "
                f"{course.location}"
            )
            for campus, details in campus_prices.items():
                print(
                    f"  {campus}: GBP "
                    f"{details['gross_annual_tuition']} per year; "
                    f"intakes {', '.join(details['advertised_full_time_intakes'])}"
                )

        except (ValueError, Course.DoesNotExist, KeyError) as exc:
            report["blocked"].append({
                "course_id": course_id,
                "reason": str(exc),
            })
            print(f"\n{course_id}: BLOCKED — {exc}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = BASE / f"campus-pg-preview-{stamp}.json"
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nCourse proposals:", len(report["proposals"]))
    print("Blocked:", len(report["blocked"]))
    print("Report:", output)
    print("Preview only. No database records changed.")


run()