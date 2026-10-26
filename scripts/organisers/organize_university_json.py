"""Organise university JSON files without running scrapers or Django."""


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


import argparse
import ast
import io
import os
from pathlib import Path
import shutil
import tokenize
from datetime import datetime, timezone


ROOT = _SCRIPT_ROOT

EXCLUDED = {
    ".git", ".venv", "venv", "venv312", "node_modules",
    "__pycache__", "logs", "command_backups", "json_backups",
}

KEEP = {"package.json", "package-lock.json"}


def destination(name):
    if name.startswith("abertay_"):
        folder = "uk/abertay"
    elif name.startswith("aberystwyth_"):
        folder = "uk/aberystwyth"
    elif name.startswith("aru_"):
        folder = "uk/aru"
    elif name.startswith("tcd_"):
        folder = "ireland/tcd"
    elif name in {"uk_universities.json", "next_uk_university_export.json"}:
        folder = "shared"
    else:
        raise ValueError(f"Unrecognised JSON file: {name}")

    return f"data/universities/{folder}/{name}"


def python_files():
    for folder, directories, files in os.walk(ROOT):
        directories[:] = [
            name for name in directories if name not in EXCLUDED
        ]
        for name in files:
            path = Path(folder) / name
            if path.suffix == ".py" and path != Path(__file__).resolve():
                yield path


def rewrite(content, mapping, filename):
    """Replace exact filename string literals; preserve other source text."""
    lines = content.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    replacements = []

    for token in tokenize.generate_tokens(io.StringIO(content).readline):
        if token.type != tokenize.STRING:
            continue

        try:
            value = ast.literal_eval(token.string)
        except (ValueError, SyntaxError):
            continue

        if not isinstance(value, str) or value not in mapping:
            continue

        start = offsets[token.start[0] - 1] + token.start[1]
        end = offsets[token.end[0] - 1] + token.end[1]
        replacements.append((start, end, repr(mapping[value])))

    for start, end, replacement in reversed(replacements):
        content = content[:start] + replacement + content[end:]

    compile(content, str(filename), "exec")
    return content, len(replacements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not (ROOT / "manage.py").exists():
        raise SystemExit("Place this script beside manage.py.")

    sources = sorted(
        path for path in ROOT.glob("*.json") if path.name not in KEEP
    )

    if not sources:
        print("No loose university JSON files found. Nothing changed.")
        return

    mapping = {
        path.name: destination(path.name)
        for path in sources
    }

    for source in sources:
        target = ROOT / mapping[source.name]
        if target.exists():
            raise SystemExit(
                f"Destination already exists; nothing changed: {target}"
            )

    edits = []
    reference_count = 0

    for path in python_files():
        original = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(
            io.BytesIO(original).readline
        )
        content = original.decode(encoding)
        updated, count = rewrite(content, mapping, path)

        if count:
            edits.append((path, original, updated.encode(encoding)))
            reference_count += count

    for source in sources:
        print(f"{source.name} -> {mapping[source.name]}")

    print(f"\nJSON files to move: {len(sources)}")
    print(f"Python files to update: {len(edits)}")
    print(f"Filename references to update: {reference_count}")

    if not args.apply:
        print("\nPreview only. No project files changed.")
        print("To apply: python organize_university_json.py --apply")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = ROOT / "json_backups" / stamp
    backup.mkdir(parents=True)

    # Finish all backups before changing project files.
    for path, original, updated in edits:
        saved = backup / path.relative_to(ROOT)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(original)

    moved = []
    written = []

    try:
        # Create destination folders before updated scripts can use them.
        for relative in mapping.values():
            (ROOT / relative).parent.mkdir(parents=True, exist_ok=True)

        for source in sources:
            target = ROOT / mapping[source.name]
            shutil.move(str(source), str(target))
            moved.append((source, target))

        for path, original, updated in edits:
            written.append((path, original))
            path.write_bytes(updated)

    except BaseException:
        for path, original in reversed(written):
            path.write_bytes(original)
        for source, target in reversed(moved):
            shutil.move(str(target), str(source))
        raise

    print("\nJSON files moved and Python paths updated.")
    print(f"Original Python files backed up in: {backup}")
    print("No scrapers or database operations executed.")


if __name__ == "__main__":
    main()