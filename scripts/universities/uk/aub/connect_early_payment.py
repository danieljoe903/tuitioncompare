"""Attach reviewed source to the comparison's temporary tuition object."""
import argparse
import ast
from datetime import datetime, timezone
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[4]
MARKER='# AUB early-payment source guard v1'
NEEDLE='        calculation = (aub_calculate if aub else calculate_scholarships)('

def patch(text):
    if MARKER in text:return text
    if text.count(NEEDLE)!=1:raise ValueError('Expected one installed AUB comparison calculation block.')
    result=text.replace(NEEDLE,'        '+MARKER+'\n        if aub:\n            comparison_tuition.source_metadata = source\n'+NEEDLE)
    ast.parse(result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    matches=[p for p in (ROOT/'app').rglob('*.py') if NEEDLE in p.read_text(encoding='utf-8-sig')]
    if len(matches)!=1:raise SystemExit('Expected one installed comparison view; no files changed.')
    path=matches[0];before=path.read_text(encoding='utf-8-sig');after=patch(before)
    print('Early-payment comparison integration: '+('unchanged' if before==after else 'update'))
    if args.apply and before!=after:
        backup=ROOT/'script_backups'/('aub-early-payment-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/path.relative_to(ROOT)
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
        path.write_text(after,encoding='utf-8');print('Applied. Backup:',backup)
    elif not args.apply:print('No files changed.')
