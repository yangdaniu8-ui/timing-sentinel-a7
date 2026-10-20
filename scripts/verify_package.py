"""Verify sealed package inputs without importing Qt or opening any device."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=ap.parse_args()
    root=a.root.resolve();m=json.loads((root/'source_manifest.json').read_bytes())
    if m.get('schema')!=1 or not isinstance(m.get('files'),dict) or not m['files']:
        raise ValueError('Invalid or empty source manifest')
    for name,item in m['files'].items():
        rel=PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name:
            raise ValueError('Unsafe manifest path: '+name)
        p=(root/name).resolve()
        if root not in p.parents:
            raise ValueError('Manifest file escapes package root: '+name)
        if not p.is_file() or p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']:
            raise ValueError('Missing or changed file: '+name)
    print(json.dumps(dict(passed=True,files=len(m['files']),source_integrity_only=True,hardware_programmed=False)))

if __name__=='__main__':main()
