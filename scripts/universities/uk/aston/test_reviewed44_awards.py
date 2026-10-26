"""Compatibility: the expanded suite now covers all 48 reviewed routes."""
from pathlib import Path
from django.conf import settings
exec((Path(settings.BASE_DIR)/'scripts/universities/uk/aston/test_pending_route_awards.py').read_text(encoding='utf-8'))
