"""Organise reviewed root scripts without executing them."""

import argparse
import ast
from datetime import datetime, timezone
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent

ABERTAY = """
abertay_fee_fallback.py
audit_abertay_coverage.py
build_abertay_mapping.py
discover_abertay.py
export_abertay_courses.py
extend_abertay_mapping.py
extract_abertay_course.py
inspect_abertay.py
inspect_abertay_course.py
inspect_abertay_fee_categories.py
inspect_abertay_links.py
inspect_abertay_remaining_policy.py
inspect_abertay_structure.py
preview_abertay_corrections.py
preview_abertay_courses.py
preview_abertay_entry_routes.py
preview_abertay_matches.py
preview_abertay_updates.py
""".split()

DESTINATIONS = {
    name: f"scripts/universities/uk/abertay/{name}"
    for name in ABERTAY
}
DESTINATIONS.update({
    "test_aberystwyth_saved_rules.py":
        "scripts/universities/uk/aberystwyth/test_aberystwyth_saved_rules.py",
    "test_aru_games_programming_rules.py":
        "scripts/universities/uk/aru/test_aru_games_programming_rules.py",
    "find_missing_courses.py":
        "scripts/maintenance/find_missing_courses.py",
    "find_missing_universities.py":
        "scripts/maintenance/find_missing_universities.py",
    "organize_university_commands.py":
        "scripts/organisers/organize_university_commands.py",
    "organize_university_json.py":
        "scripts/organisers/organize_university_json.py",
})

HELPERS = {
    "extract_abertay_course":
        "scripts.universities.uk.abertay.extract_abertay_course",
    "abertay_fee_fallback":
        "scripts.universities.uk.abertay.abertay_fee_fallback",
}

COMMAND_FOLDER = ROOT / "app/scraping/uk/abertay/commands"

BOOTSTRAP = '''
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
'''


def rewrite(source, filename, relocated=False, launcher=False):
    tree = ast.parse(source)
    raw = source.encode("utf-8")
    offsets = [0]

    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    replacements = []

    def replace(node, value):
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        replacements.append((start, end, value.encode("utf-8")))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in HELPERS:
            if node.level:
                raise ValueError(f"Unexpected relative import: {filename}")
            imported = ", ".join(
                item.name + (f" as {item.asname}" if item.asname else "")
                for item in node.names
            )
            replace(
                node,
                f"from {HELPERS[node.module]} import {imported}",
            )

        if relocated and isinstance(node, ast.Attribute):
            expression = ast.get_source_segment(source, node)
            if expression == "Path(__file__).resolve().parent":
                replace(node, "_SCRIPT_ROOT")

        if (
            launcher
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in DESTINATIONS
        ):
            replace(node, repr(DESTINATIONS[node.value]))

    for start, end, value in sorted(replacements, reverse=True):
        raw = raw[:start] + value + raw[end:]

    updated = raw.decode("utf-8")

    if relocated:
        parsed = ast.parse(updated)
        insertion_line = 0

        for index, node in enumerate(parsed.body):
            is_docstring = (
                index == 0
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
            is_future = (
                isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
            )
            if is_docstring or is_future:
                insertion_line = node.end_lineno
            else:
                break

        lines = updated.splitlines(keepends=True)
        lines.insert(insertion_line, "\n" + BOOTSTRAP + "\n")
        updated = "".join(lines)

    compile(updated, filename, "exec")
    return updated.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()

    if not (ROOT / "manage.py").is_file():
        raise ValueError("Place this organiser beside manage.py.")

    # Each entry records the original and intended file contents.
    plan = {}

    def add(path, after):
        before = path.read_bytes() if path.exists() else None
        if before != after:
            plan[path] = (before, after)

    for name, relative in DESTINATIONS.items():
        source = ROOT / name
        target = ROOT / relative

        if not source.is_file():
            raise ValueError(f"Expected root script is missing: {name}")
        if target.exists():
            raise ValueError(f"Destination already exists: {relative}")

        content = source.read_text(encoding="utf-8-sig")
        updated = rewrite(content, relative, relocated=True)

        add(target, updated)
        add(source, None)
        print(f"{name} -> {relative}")

        for folder in target.parent.parents:
            if folder == ROOT:
                break
            if folder.is_relative_to(ROOT / "scripts"):
                init = folder / "__init__.py"
                if not init.exists():
                    add(init, b"")

        init = target.parent / "__init__.py"
        if not init.exists():
            add(init, b"")

    callers = [
        COMMAND_FOLDER / "preview_abertay_remaining.py",
        COMMAND_FOLDER / "scrape_abertay.py",
    ]

    for path in callers:
        if not path.is_file():
            raise ValueError(f"Expected caller is missing: {path}")

        updated = rewrite(
            path.read_text(encoding="utf-8-sig"),
            str(path),
            launcher=path.name == "scrape_abertay.py",
        )
        add(path, updated)

    print(f"\nScripts to move: {len(DESTINATIONS)}")
    print("manage.py and country_number.py: untouched")
    print("Logs, JSON data and database: untouched")
    print("No scripts or scrapers executed.")

    if not options.apply:
        print("\nPreview only. No project files changed.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = ROOT / "script_backups" / stamp
    backup.mkdir(parents=True)

    # Back up every existing file before making changes.
    for path, (before, after) in plan.items():
        if before is not None:
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)

    applied = []

    try:
        # Write destinations and caller corrections before removing originals.
        ordered = sorted(
            plan.items(),
            key=lambda item: item[1][1] is None,
        )

        for path, (before, after) in ordered:
            current = path.read_bytes() if path.exists() else None
            if current != before:
                raise ValueError(f"File changed during planning: {path}")

            applied.append(path)

            if after is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(after)

    except BaseException:
        for path in reversed(applied):
            before = plan[path][0]
            if before is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(before)
        raise

    print("\nOrganisation applied.")
    print("Backup:", backup)

    # Move this organiser last, keeping the root clean.
    own_target = ROOT / "scripts/organisers" / Path(__file__).name
    if not own_target.exists():
        shutil.move(str(Path(__file__).resolve()), str(own_target))
        print("This organiser moved to:", own_target)

    print("Next: python manage.py check")


if __name__ == "__main__":
    main()