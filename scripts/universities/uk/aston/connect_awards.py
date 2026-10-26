"""Add saved scholarship-page facts to the already connected Aston view."""
import ast,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[4]
MARKER='# Aston reviewed30 awards v1'
def patch(text):
    if MARKER in text:return text
    a='fee_metadata as aston_metadata, calculate as aston_calculate'
    b='fee_metadata as aston_metadata, saved_profile as aston_profile, calculate as aston_calculate'
    c='        course_profile = dict(profile)'
    d=c+'\n        '+MARKER+'\n        if aston:\n            course_profile.update(aston_profile(request, course.course_id, academic_year))'
    e='        if aup:\n            comparison_tuition.source_metadata = source'
    f='        if aup or aston:\n            comparison_tuition.source_metadata = source'
    for old,new in [(a,b),(c,d),(e,f)]:
        if text.count(old)!=1:raise ValueError('Installed comparison layout differs; no edit: '+old)
        text=text.replace(old,new,1)
    old='scholarship_info = "Aston scholarship applicability requires exact-cohort review. Visit the scholarship page."'
    new='scholarship_info = "Confirmed Aston award deducted." if discount > 0 else "Aston award conditions require saved confirmation. Visit the scholarship page."'
    if text.count(old)!=1:raise ValueError('Aston result message layout changed.')
    text=text.replace(old,new,1);ast.parse(text);return text
def run(apply=False):
    matches=[]
    for p in (ROOT/'app').rglob('*.py'):
        t=p.read_text(encoding='utf-8-sig')
        if '# Aston first30 comparison v1' in t and 'def compare_tuition(' in t:matches.append((p,t))
    if len(matches)!=1:raise ValueError('Expected one installed Aston comparison view.')
    p,old=matches[0];new=patch(old);print('Aston awards integration:', 'unchanged' if old==new else 'update')
    if apply and old!=new:
        backup=ROOT/'script_backups'/('aston-awards-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/p.relative_to(ROOT)
        backup.parent.mkdir(parents=True);shutil.copy2(p,backup);p.write_text(new,encoding='utf-8');print('Applied. Backup:',backup)
    elif not apply:print('No files changed.')
if __name__=='__main__':run('--apply' in sys.argv)
