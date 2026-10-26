"""Preview/apply a reversible organisation of university management commands.

Run from the Django project root. This script does not import Django, run any
scraper, contact a university, or change database records.
"""

import argparse
import ast
import hashlib
import json
import os
import pprint
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


GROUPS = {
    "abertay": "app.scraping.uk.abertay.commands",
    "aberystwyth": "app.scraping.uk.aberystwyth.commands",
    "aru": "app.scraping.uk.aru.commands",
    "arden": "app.scraping.uk.arden.commands",
    "tcd": "app.scraping.ireland.tcd.commands",
}
IGNORE = {
    ".git", ".venv", "venv", "venv312", "__pycache__", "node_modules",
    "command_backups", "logs", "site-packages", ".tox", ".mypy_cache",
}
OLD_PACKAGE = "app.management.commands"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def group_for(stem):
    return next((g for g in GROUPS if g in stem.split("_")), None)


def action_for(stem, group):
    return "_".join(part for part in stem.split("_") if part != group)


def module_path(module):
    return Path(*module.split(".")).with_suffix(".py")


def rewrite_imports(source, mapping):
    tree = ast.parse(source)
    encoded = source.encode("utf-8")
    lines = encoded.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []

    def alias_text(alias):
        return alias.name + (" as " + alias.asname if alias.asname else "")

    for node in ast.walk(tree):
        replacement = None
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 1:
                full = OLD_PACKAGE + ("." + module if module else "")
            elif node.level == 0:
                full = module
            else:
                continue

            if full in mapping:
                replacement = "from " + mapping[full] + " import " + ", ".join(
                    alias_text(alias) for alias in node.names
                )
            elif full == OLD_PACKAGE:
                parts = []
                for alias in node.names:
                    old = OLD_PACKAGE + "." + alias.name
                    if old not in mapping:
                        raise ValueError("Unsupported package import: " + ast.unparse(node))
                    parent = mapping[old].rsplit(".", 1)[0]
                    parts.append("from " + parent + " import " + alias_text(alias))
                replacement = ("\n" + " " * node.col_offset).join(parts)
            elif full == "django.core.management":
                moved = [a for a in node.names if a.name in {"call_command", "get_commands"}]
                kept = [a for a in node.names if a.name not in {"call_command", "get_commands"}]
                if moved:
                    parts = []
                    if kept:
                        parts.append("from django.core.management import " + ", ".join(map(alias_text, kept)))
                    parts.append("from app.scraping.command_compat import " + ", ".join(map(alias_text, moved)))
                    replacement = ("\n" + " " * node.col_offset).join(parts)

        elif isinstance(node, ast.Import):
            if any(a.name in mapping for a in node.names):
                parts = []
                for alias in node.names:
                    if alias.name in mapping and not alias.asname:
                        raise ValueError("Add an explicit alias before moving: " + ast.unparse(node))
                    name = mapping.get(alias.name, alias.name)
                    parts.append(name + (" as " + alias.asname if alias.asname else ""))
                replacement = "import " + ", ".join(parts)

        if replacement is not None:
            start = offsets[node.lineno - 1] + node.col_offset
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            edits.append((start, end, replacement.encode("utf-8")))

    for start, end, value in sorted(edits, reverse=True):
        encoded = encoded[:start] + value + encoded[end:]
    result = encoded.decode("utf-8")
    ast.parse(result)
    return result, len(edits)


COMPAT = '''"""Compatibility for command-to-command calls after relocation."""
from importlib import import_module
from django.core.management import call_command as django_call_command
from django.core.management import get_commands as django_get_commands
from .command_registry import COMMANDS


def call_command(command_name, *args, **kwargs):
    if isinstance(command_name, str) and command_name in COMMANDS:
        command = import_module(COMMANDS[command_name]["module"]).Command()
        return django_call_command(command, *args, **kwargs)
    return django_call_command(command_name, *args, **kwargs)


def get_commands():
    result = dict(django_get_commands())
    for name in COMMANDS:
        result.setdefault(name, "app")
    return result
'''


DISPATCHER = '''"""Run one existing university operation through its grouped interface."""
from importlib import import_module
from django.core.management.base import BaseCommand, CommandError
from app.scraping.command_registry import COMMANDS


class Command(BaseCommand):
    help = "List/run university operations: university_command aru inspect_sources"

    def add_arguments(self, parser):
        parser.add_argument("university", choices=sorted({v["group"] for v in COMMANDS.values()}))
        parser.add_argument("action", nargs="?")
        parser.add_argument("command_arguments", nargs="...")

    def handle(self, *args, **options):
        group = options["university"]
        operations = {v["action"]: (name, v) for name, v in COMMANDS.items() if v["group"] == group}
        action = options["action"]
        if action is None:
            for key in sorted(operations):
                self.stdout.write(key)
            return
        if action in COMMANDS and COMMANDS[action]["group"] == group:
            name, entry = action, COMMANDS[action]
        elif action in operations:
            name, entry = operations[action]
        else:
            raise CommandError("Unknown operation. List operations with: university_command " + group)
        command = import_module(entry["module"]).Command(stdout=self.stdout, stderr=self.stderr)
        command.run_from_argv(["manage.py", name, *options["command_arguments"]])
'''


def ensure_inside(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes project: " + str(relative))
    if path.is_symlink():
        raise ValueError("Symlink requires manual review: " + str(relative))
    return path


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".organizing.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def restore(root, backup):
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest["project_root"]).resolve() != root.resolve():
        raise ValueError("This backup belongs to a different project root.")
    for item in manifest["files"]:
        path = ensure_inside(root, item["path"])
        actual = sha(path.read_bytes()) if path.is_file() else None
        if actual not in {item["after_sha256"], item["before_sha256"]}:
            raise ValueError("File changed since organisation; preserve it before restore: " + item["path"])
        if item["before_sha256"] is not None:
            saved = backup / "original" / item["path"]
            if sha(saved.read_bytes()) != item["before_sha256"]:
                raise ValueError("Backup checksum failed: " + item["path"])
    for item in reversed(manifest["files"]):
        path = ensure_inside(root, item["path"])
        if item["before_sha256"] is None:
            if path.exists():
                path.unlink()
        else:
            atomic_write(path, (backup / "original" / item["path"]).read_bytes())
    print("Original files restored. Backup retained:", backup)


def build_plan(root, compact):
    command_dir = root / "app" / "management" / "commands"
    if not (root / "manage.py").is_file() or not command_dir.is_dir():
        raise ValueError("Run this from the Django project root containing manage.py.")
    registry_path = root / "app" / "scraping" / "command_registry.py"
    if registry_path.exists():
        raise ValueError("An organisation registry already exists. Use it, or restore its backup before reorganising again.")
    files = sorted(command_dir.glob("*.py"))
    selected = {p.stem: p for p in files if not p.stem.startswith("_") and group_for(p.stem)}
    if not selected:
        raise ValueError("No supported university commands found.")
    mapping = {OLD_PACKAGE + "." + name: GROUPS[group_for(name)] + "." + name for name in selected}
    plans = {}
    registry = {}
    counts = Counter()
    import_edits = 0

    def add(relative, after, must_be_new=False):
        path = ensure_inside(root, relative)
        if must_be_new and path.exists():
            raise ValueError("Destination exists; no overwrite allowed: " + str(relative))
        before = path.read_bytes() if path.is_file() else None
        if before != after:
            plans[str(relative).replace("\\", "/")] = (before, after)

    for name, path in selected.items():
        source = path.read_bytes().decode("utf-8-sig")
        tree = ast.parse(source)
        if any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(tree)):
            raise ValueError("__file__ use needs manual relocation review: " + name)
        group = group_for(name)
        action = action_for(name, group)
        if any(v["group"] == group and v["action"] == action for v in registry.values()):
            raise ValueError("Grouped action collision: " + group + " " + action)
        new_module = mapping[OLD_PACKAGE + "." + name]
        rewritten, edits = rewrite_imports(source, mapping)
        import_edits += edits
        add(module_path(new_module), rewritten.encode("utf-8"), must_be_new=True)
        registry[name] = {"group": group, "action": action, "module": new_module}
        counts[group] += 1
        relative = path.relative_to(root)
        if compact:
            add(relative, None)
        else:
            wrapper = (
                '"""Compatibility alias; implementation is in app.scraping."""\n'
                "from importlib import import_module\n"
                "import sys\n"
                "sys.modules[__name__] = import_module(" + repr(new_module) + ")\n"
            )
            add(relative, wrapper.encode("utf-8"))

    # In compact mode, external old Python imports must be reviewed first.
    # Keep-alias mode preserves them through the compatibility modules.
    if compact:
        blocked = []
        for current, directories, filenames in os.walk(root):
            directories[:] = [name for name in directories if name not in IGNORE]
            for filename in filenames:
                path = Path(current) / filename
                relative = path.relative_to(root)
                if path.suffix.lower() not in {".py", ".sh", ".bat", ".cmd", ".ps1"}:
                    continue
                if path.resolve() == Path(__file__).resolve():
                    continue
                if relative.parts[:3] == ("app", "management", "commands"):
                    continue
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if OLD_PACKAGE in text or any(name in text for name in selected):
                    blocked.append(str(relative))
        # General top-level commands are also outside the relocated package.
        for path in files:
            if path.stem in selected:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if OLD_PACKAGE in text or any(name in text for name in selected):
                blocked.append(str(path.relative_to(root)))
        if blocked:
            raise ValueError("Compact mode requires review of external references. Use the default alias mode first. Files: " + ", ".join(sorted(set(blocked))))

    registry_text = '"""Existing operations; generated without executing scrapers."""\nCOMMANDS = ' + pprint.pformat(registry, sort_dicts=True, width=100) + "\n"
    add(Path("app/scraping/command_registry.py"), registry_text.encode("utf-8"), True)
    add(Path("app/scraping/command_compat.py"), COMPAT.encode("utf-8"), True)
    add(Path("app/management/commands/university_command.py"), DISPATCHER.encode("utf-8"), True)
    directories = set()
    for relative in list(plans):
        path = Path(relative)
        if path.parts[:2] != ("app", "scraping"):
            continue
        for parent in path.parents:
            if parent == Path(".") or parent == Path("app"):
                break
            directories.add(parent)
    for directory in sorted(directories):
        init = directory / "__init__.py"
        if not (root / init).exists():
            add(init, b"")
    for relative, (_, after) in plans.items():
        if after is not None and relative.endswith(".py"):
            compile(after, relative, "exec")
    return plans, registry, counts, import_edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Remove old command aliases; old terminal names are replaced by the grouped interface.")
    parser.add_argument("--restore", type=Path, help="Restore the exact backup directory printed by a previous apply.")
    options = parser.parse_args()
    root = options.root.resolve()
    if options.restore:
        restore(root, options.restore.resolve())
        return
    plans, registry, counts, edits = build_plan(root, options.compact)
    print("University command implementations:", len(registry))
    for group, count in sorted(counts.items()):
        print(" ", group + ":", count)
    print("Import statements rewritten:", edits)
    print("Mode:", "compact: grouped interface replaces old terminal names" if options.compact else "compatibility: old terminal names remain available")
    print("New interface: python manage.py university_command aru inspect_sources")
    print("No scrapers or database operations are executed by this organiser.")
    if not options.apply:
        print("Preview only. No project files changed.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = root / "command_backups" / stamp
    backup.mkdir(parents=True)
    manifest = {"project_root": str(root), "mode": "compact" if options.compact else "compatibility", "commands": registry, "files": []}
    for relative, (before, after) in plans.items():
        if before is not None:
            saved = backup / "original" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)
        manifest["files"].append({"path": relative, "before_sha256": sha(before) if before is not None else None, "after_sha256": sha(after) if after is not None else None})
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    applied = []
    try:
        for relative, (before, after) in plans.items():
            path = ensure_inside(root, relative)
            current = path.read_bytes() if path.is_file() else None
            if current != before:
                raise ValueError("Project file changed after planning: " + relative)
            if after is None:
                path.unlink()
            else:
                atomic_write(path, after)
            applied.append(relative)
    except BaseException:
        for relative in reversed(applied):
            path = root / relative
            before = plans[relative][0]
            if before is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write(path, before)
        raise
    print("Organisation applied. Backup:", backup)
    print("Restore: python organize_university_commands.py --restore \"" + str(backup) + "\"")
    print("Next: python manage.py check")
    print("List ARU operations: python manage.py university_command aru")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, SyntaxError) as exc:
        print("Stopped:", exc, file=sys.stderr)
        sys.exit(1)
