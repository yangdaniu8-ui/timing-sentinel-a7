"""Offline evidence verification. Never opens serial/JTAG or programs a device."""
import argparse
import collections
import csv
import hashlib
import json
import importlib.util
from pathlib import Path, PurePosixPath
import sys
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def safe(root, name):
    require(isinstance(name,str) and bool(name), 'Invalid evidence path')
    p = PurePosixPath(name)
    require(not p.is_absolute() and '..' not in p.parts and ':' not in name and '\\' not in name,
            'Unsafe evidence path')
    target = (root/name).resolve()
    require(root in target.parents and target.is_file() and not (root/name).is_symlink(), 'Missing/unsafe file: '+name)
    return target


def metadata(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == 4 and set(names) == {'manifest.json','metadata.json','samples.bin','events.bin'}, 'Invalid record members')
        require(sum(i.file_size for i in archive.infolist()) <= 1_000_000, 'Oversized record')
        manifest = json.loads(archive.read('manifest.json'))
        require(manifest['schema_version']==2 and manifest['profile']=='mvp5', 'Unexpected record schema/profile')
        require(set(manifest['files']) == {'metadata.json','samples.bin','events.bin'}, 'Invalid record manifest')
        data = {}
        for name, entry in manifest['files'].items():
            b = archive.read(name)
            require(len(b) == entry['size'] and hashlib.sha256(b).hexdigest() == entry['sha256'], 'Record payload mismatch')
            data[name] = b
        m = json.loads(data['metadata.json'])
        require(manifest['transfer_complete'] is True and m['origin'] == 'hardware', 'Incomplete/non-hardware record')
        require(len(data['samples.bin']) == 65536, 'Unexpected sample count')
        require(manifest['record_id'] == m['record_id'], 'Record identity mismatch')
        return m


def verify(root, replay=False, host=None):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_bytes())
    require(manifest['schema'] == 1 and manifest['files'], 'Invalid evidence manifest')
    for name, expected in manifest['files'].items():
        p = safe(root, name)
        require(p.stat().st_size == expected['bytes'] and sha(p) == expected['sha256'], 'Evidence changed: '+name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    require(actual == set(manifest['files'])|{'manifest.json'}, 'Evidence inventory mismatch')
    index = json.loads((root/'index.json').read_bytes())
    cells = collections.Counter()
    identities = set()
    physical = index['soak600']['records']
    require(len(physical) == 600 and len({r['job'] for r in physical}) == 600, 'Wrong task count')
    require(sorted(r['job'] for r in physical) == list(range(9,609)), 'Task IDs not consecutive')
    if replay:
        host = Path(host or root.parent/'host').resolve()
        require((host/'mvp5/analysis.py').is_file(), 'For --replay provide --host pointing to delivered host source')
        sys.path.insert(0, str(host))
        from mvp5.store import load_record
        from mvp5.analysis import audit_capture
        from mvp5.workload import identify_workload
    replayed = 0
    for number, row in enumerate(physical, 1):
        p = safe(root, row['path']); m = metadata(p)
        require(sha(p) == row['sha256'], 'Case hash mismatch')
        require((m['session_id'],m['config_id']) == (row['session'],row['config']), 'Case identity mismatch')
        require(m['record_id'] not in identities, 'Duplicate record counted as new task')
        identities.add(m['record_id'])
        require(m['enabled_mask'] == 5 and m['sample_divisor'] == 10, 'Unexpected record configuration')
        require(m['config']['rules'][0]['max_ticks']/50000 == row['deadline_ms'], 'Deadline mismatch')
        expected = 'TIMEOUT' if row['deadline_ms'] == 3.5 and row['profile'] == 2 else 'PASS'
        require([m['rules'][i]['result'] for i in (0,2)] == [expected,expected], 'Unexpected rule verdict')
        cells[(row['deadline_ms'],row['profile'])] += 1
        if replay:
            cap = load_record(p); audit = audit_capture(cap); identity = identify_workload(cap)
            require(audit['status'] == 'verified' and identity and identity['verified'], 'Raw sample replay failed')
            require(identity['workload']['id'] == row['job'] and identity['workload']['profile'] == row['profile'], 'Decoded workload mismatch')
            replayed += 1
        if number % 100 == 0:
            print('EVIDENCE_PROGRESS', number, 'of 600', flush=True)
    require(cells == collections.Counter({(d,w):100 for d in (3.5,8) for w in (0,1,2)}), 'Cell distribution mismatch')
    ila = index['ila']['cases']
    require(len(ila) == 12 and sum(r['matched_events'] for r in ila) == 256, 'Wrong ILA scope')
    for row in ila:
        m = metadata(safe(root, row['record']))
        require((m['session_id'],m['config_id']) == (row['session'],row['config']), 'ILA pairing mismatch')
        with safe(root,row['csv']).open(encoding='utf-8-sig',newline='') as f:
            rows = list(csv.DictReader(f))
        require(len(rows) == 1025 and rows[0]['Sample in Buffer'].startswith('Radix'), 'Wrong ILA CSV depth')
    ila_replayed = 0
    if replay:
        require(__debug__, 'Do not use Python -O: the preserved ILA checker uses assertions')
        script = root.parent/'scripts/verify_ila_capture.py'
        require(script.is_file(), 'Missing paired ILA comparison module')
        spec = importlib.util.spec_from_file_location('paired_ila', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for row in ila:
            result = module.compare(module.read_csv(safe(root,row['csv'])),load_record(safe(root,row['record'])))
            require(result['passed'] and result['session']==row['session'] and result['config']==row['config']
                    and result['matched_events_after_a']==row['matched_events'], 'ILA event replay differs')
            ila_replayed += 1
    return dict(passed=True, manifest_files=len(manifest['files']), physical_tasks_in_archive=600,
                physical_sample_points=600*65536, enabled_rule_verdicts=1200,
                all_record_ids_unique=True, cell_counts=[dict(deadline_ms=d,profile=w,n=n) for (d,w),n in sorted(cells.items())],
                ila_cases=12, ila_rows=12288, matched_ila_events=256, raw_sample_replays=replayed, ila_event_replays=ila_replayed,
                new_hardware_transactions=0, serial_opened=False, evidence_files_unchanged=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]/'evidence')
    ap.add_argument('--replay',action='store_true'); ap.add_argument('--host',type=Path)
    args = ap.parse_args()
    try:
        result = verify(args.root,args.replay,args.host)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    except (ValueError,KeyError,OSError,AssertionError,zipfile.BadZipFile) as exc:
        print(json.dumps(dict(passed=False,error=str(exc)),ensure_ascii=False)); return 1
    return 0


if __name__ == '__main__': sys.exit(main())
