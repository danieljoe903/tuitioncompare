"""Add Alumni-specific profile answers without modifying Global Tiers facts."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import shutil
import sys
ROOT = Path(__file__).resolve().parents[4]
TARGET = ROOT / 'app/scraping/uk/arden/route_calculation.py'
MARKER = '# Online Alumni profile integration v7'
FACTS = {
 'arden_alumni_qualifying_degree': 'Has Arden verified that you obtained at least a 2:2 and 300 credits on an eligible Arden undergraduate programme?',
 'arden_alumni_within_five_years': 'Has Arden verified that your undergraduate graduation falls within the five years before this postgraduate programme starts?',
 'arden_alumni_official_results_received': 'Have you received your official qualifying undergraduate results?',
 'arden_alumni_advisor_informed': 'Have you told your educational advisor that you wish to use the online Alumni offer?',
 'arden_alumni_offer_granted': 'Has Arden granted you the 50% online Postgraduate Alumni offer for this programme and intake?',
}

def run(apply=False):
    old = TARGET.read_text(encoding='utf-8')
    if MARKER in old:
        if not all(k in old for k in FACTS):
            raise ValueError('Incomplete Alumni integration; review required.')
        print('Online Alumni integration: unchanged')
        return
    new = old
    anchor = "    profile.update(arden_campus='Online', arden_intake=intake,"
    if new.count(anchor) != 1:
        raise ValueError('Cannot identify the online profile insertion point.')
    new = new.replace(anchor, "    profile.update({key: campus.boolean(request.GET.get(key)) for key in ALUMNI_FACTS})\n" + anchor)
    anchor = "    questions.extend([\n        {'key': 'arden_application_residence'"
    if new.count(anchor) != 1:
        raise ValueError('Cannot identify the online question insertion point.')
    new = new.replace(anchor, "    questions.extend({'key': key, 'label': label, 'type': 'boolean'} for key, label in ALUMNI_FACTS.items())\n" + anchor)
    new += '\n\n' + MARKER + '\nALUMNI_FACTS = ' + repr(FACTS) + '\n'
    ast.parse(new)
    print('Online Alumni integration: update')
    if apply:
        backup = ROOT / 'script_backups' / ('arden-online-alumni-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(TARGET, backup / TARGET.name)
        TARGET.write_text(new, encoding='utf-8')
        print('Applied. Backup:', backup)
    else:
        print('Preview only. No files changed. Use --apply to connect.')

if __name__ == '__main__':
    run(apply='--apply' in sys.argv)
