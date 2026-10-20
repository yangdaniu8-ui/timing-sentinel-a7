"""Run every delivered host test file in its own process; never open COM."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    report = dict(passed=False, scope='offline per-file isolated processes', serial_opened=False, new_hardware_transactions=0, suites=[])
    try:
        for start in ('mvp5/tests', 'mvp4/tests', 'tests', 'app/tests'):
            files=sorted((ROOT/'host'/start).glob('test_*.py'))
            if not files: raise RuntimeError('Missing test group: '+start)
            for source in files:
                tag=start.replace('/','_')+'_'+source.stem
                try:
                    p = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', start, '-p', source.name, '-v'], cwd=ROOT/'host', env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
                    log = p.stdout + '\n' + p.stderr
                    code=p.returncode
                except subprocess.TimeoutExpired as error:
                    log=str(error);code='timeout'
                (args.out/f'{tag}.log').write_text(log, encoding='utf-8')
                match = re.search(r'Ran (\d+) tests? in', log)
                actual = int(match[1]) if match else 0
                passed=code==0 and actual>0 and bool(re.search(r'\nOK(?:\s|$)',log))
                report['suites'].append(dict(name=start+'/'+source.name, exit_code=code, tests=actual, passed=passed))
                print(tag,'PASS' if passed else 'FAIL',actual,flush=True)
        report['passed'] = all(x['passed'] for x in report['suites'])
        report['tests'] = sum(x['tests'] for x in report['suites'])
    finally:
        (args.out/'verified.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
