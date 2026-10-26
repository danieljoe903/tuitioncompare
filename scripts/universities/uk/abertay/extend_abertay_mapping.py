

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


ROOT = _SCRIPT_ROOT
TARGETS = {
    "C_UK001_10": {
        "path": "mental-health-nursing",
        "duration": "3 years (full-time)",
        "allowed_flags": set(),
    },
    "C_UK001_79": {
        "path": "game-design-and-production",
        "duration": "4 years (full-time)",
        "allowed_flags": set(),
    },
    "C_UK001_44": {
        "path": "sport-and-management",
        "duration": "4 years (full-time)",
        "allowed_flags": set(),
    },
}

def main():
    mapping_path = ROOT / 'data/universities/uk/abertay/abertay_course_mapping.json'

    mapping = json.loads(
        mapping_path.read_text(encoding="utf-8-sig")
    )
    report = json.loads(
        (ROOT / 'data/universities/uk/abertay/abertay_matches_preview.json').read_text(
            encoding="utf-8-sig"
        )
    )

    entries = mapping["mappings"]
    added = 0

    for course_id, target in TARGETS.items():
        source_url = (
            "https://www.abertay.ac.uk/course-search/"
            f'undergraduate/{target["path"]}/'
        )

        matches = [
            row
            for row in report["source_courses"]
            if row["source_url"] == source_url
        ]

        if len(matches) != 1:
            raise ValueError(
                f"{course_id}: expected one source record."
            )

        source = matches[0]
        candidates = source["candidates"]

        if (
            len(candidates) != 1
            or candidates[0]["course_id"] != course_id
        ):
            raise ValueError(
                f"{course_id}: database candidate differs."
            )

        candidate = candidates[0]

        if not set(candidate["flags"]).issubset(
            target["allowed_flags"]
        ):
            raise ValueError(
                f"{course_id}: unexpected matching flags."
            )

        if source["source_duration"] != target["duration"]:
            raise ValueError(
                f"{course_id}: source duration changed."
            )

        existing_entries = [
            entry
            for entry in entries
            if entry["course_id"] == course_id
            or entry["source_url"] == source_url
        ]

        if existing_entries:
            if (
                len(existing_entries) == 1
                and existing_entries[0]["course_id"] == course_id
                and existing_entries[0]["source_url"] == source_url
            ):
                continue

            raise ValueError(
                f"{course_id}: conflicting mapping already exists."
            )

        entries.append({
            "course_id": course_id,
            "source_url": source_url,
            "expected_stored_title": candidate["stored_title"],
            "expected_stored_duration": candidate["stored_duration"],
            "expected_stored_level": candidate["stored_level"],
            "source_title": source["source_title"],
            "source_award": source["source_award"],
            "source_duration": source["source_duration"],
            "approved": False,
        })

        added += 1

    # Preserve the original mapping before writing.
    backup_path = ROOT / 'data/universities/uk/abertay/abertay_course_mapping.before_extension.json'

    if not backup_path.exists():
        backup_path.write_bytes(mapping_path.read_bytes())

    temporary = mapping_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(mapping_path)

    print(f"Added: {added}")
    print(f"Total mappings: {len(entries)}")
    print("Existing approvals preserved.")
    print("No database records changed.")


if __name__ == "__main__":
    main()