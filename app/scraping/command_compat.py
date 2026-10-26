"""Compatibility for command-to-command calls after relocation."""
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
