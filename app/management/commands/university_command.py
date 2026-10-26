"""Run one existing university operation through its grouped interface."""
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
