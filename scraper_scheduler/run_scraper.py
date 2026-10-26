"""Run the existing Django fee command. No third-party scheduler dependency."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta


def acquire_lock(path):
    handle = path.open('a+b')
    handle.seek(0, 2)
    if not handle.tell():
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        # use on windows
        if os.name == 'nt': 
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            # use on linux/unix
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle  # Keep open until all child processes finish; OS releases on exit.


def run(config_path, preview=False):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    year = config['academic_year']
    if not re.fullmatch(r'\d{4}/\d{2}', year) or int(year[5:]) != (int(year[:4]) + 1) % 100:
        raise ValueError('academic_year must be consecutive, e.g. 2026/27')
    root = Path(config['project_root']).resolve()
    python = Path(config['python_executable']).resolve()
    if not (root / 'manage.py').is_file() or not python.is_file():
        raise ValueError('Check project_root and python_executable in config.json')
    timeout = int(config.get('timeout_seconds', 900))
    retention = int(config.get('log_retention_days', 90))
    if timeout < 1 or retention < 1:
        raise ValueError('Timeout and log retention must be positive')
    mapping = config.get('mapping_file')
    if mapping:
        mapping = Path(mapping)
        if not mapping.is_absolute():
            mapping = root / mapping
        if not mapping.is_file():
            raise ValueError(f'Mapping file does not exist: {mapping}')
    logs = root / 'logs' / 'tcd-fees'
    logs.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(logs / 'runner.lock')
    if lock is None:
        print('Skipped: another scheduler runner is active.', flush=True)
        return 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        for old in logs.glob('run-*.log'):
            if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
                old.unlink()
        started = datetime.now(timezone.utc)
        log_path = logs / ('run-' + started.strftime('%Y%m%dT%H%M%S%fZ') + '.log')
        status = {'started_at': started.isoformat(), 'academic_year': year,
                  'mode': 'preview' if preview else 'apply', 'log_file': str(log_path),
                  'status': 'running', 'exit_code': None}
        status_path = logs / ('preview-status.json' if preview else 'last-status.json')
        def write_status():
            temporary = status_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(status, indent=2), encoding='utf-8')
            temporary.replace(status_path)
        write_status()
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        exit_code = 1
        with log_path.open('w', encoding='utf-8') as log:
            log.write(f'TCD fees {year}; {status["mode"]}; started {started.isoformat()}\n')
            log.flush()
            try:
                # Refuse updates when migrations are pending; never apply them automatically.
                schema_check = (
                    'from app.models import TuitionSource, TuitionUpdateAudit; '
                    'from django.db import connection; '
                    'tables = set(connection.introspection.table_names()); '
                    'required = {TuitionSource._meta.db_table, TuitionUpdateAudit._meta.db_table}; '
                    'missing = required - tables; '
                    'assert not missing, "Missing fee tables: " + str(sorted(missing))'
                )
                commands = [[str(python), str(root / 'manage.py'), 'migrate', '--check', '--noinput'],
                            [str(python), str(root / 'manage.py'), 'shell', '-c', schema_check],
                            [str(python), str(root / 'manage.py'), 'scrape_tcd_fees',
                             '--academic-year', year, '--limit', '1']]
                if not preview:
                    commands[-1].append('--apply')
                if mapping:
                    commands[-1].extend(['--mapping', str(mapping)])
                if config.get('university_id'):
                    commands[-1].extend(['--university-id', str(config['university_id'])])
                for command in commands:
                    log.write('\nRunning: ' + subprocess.list2cmdline(command) + '\n')
                    log.flush()
                    completed = subprocess.run(command, cwd=root, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, timeout=timeout)
                    exit_code = completed.returncode
                    if exit_code:
                        break
                status['status'] = 'success' if exit_code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                log.write('\nFAILED: command timed out. Inspect the log and database before retrying.\n')
                status['status'] = 'timeout'
                exit_code = 124
            except Exception as exc:
                log.write(f'\nFAILED: {type(exc).__name__}: {exc}\n')
                status['status'] = 'failed'
                exit_code = 1
            status.update(exit_code=exit_code, finished_at=datetime.now(timezone.utc).isoformat())
            log.write(f'\nFinished: {status["status"]}, exit code {exit_code}\n')
        write_status()
        print(f'{status["status"]}: {log_path}', flush=True)
        return exit_code
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    try:
        return run(args.config.resolve(), args.preview)
    except Exception as exc:
        print(f'Scheduler configuration error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
