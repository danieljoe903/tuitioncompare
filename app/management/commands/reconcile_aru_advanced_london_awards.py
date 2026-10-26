"""Compatibility alias; implementation is in app.scraping."""
from importlib import import_module
import sys
sys.modules[__name__] = import_module('app.scraping.uk.aru.commands.reconcile_aru_advanced_london_awards')
