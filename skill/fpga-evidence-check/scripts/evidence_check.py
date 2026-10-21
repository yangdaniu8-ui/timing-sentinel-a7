"""Offline selected-file integrity and numeric golden CSV checks; Python 3.10+."""
import argparse
import csv
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys


def finite(value):
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal') from exc
    if not number.is_finite():
        raise ValueError('Non-finite decimal')
    if len(number.as_tuple().digits) > 4096 or abs(number.as_tuple().exponent) > 4096:
        raise ValueError('Decimal exceeds supported size')
    return number


def safe_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Unsafe relative path')
    rel = PurePosixPath(name)
    if rel.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('Unsafe relative path')
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Root is not a directory')
    target = root
    for part in rel.parts:
        target = target / part
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links/reparse points are unsupported')
    if not target.is_file() or root not in target.resolve().parents:
        raise ValueError('Not a regular file under root')
    return target


def fingerprint(path):
    before = path.stat()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Input changed during hashing')
    return dict(bytes=after.st_size, sha256=h.hexdigest())


def seal(root, names, output):
    if not names or len(names) != len(set(names)):
        raise ValueError('File list must be nonempty and unique')
    output = Path(output)
    files = {}
    for name in sorted(names):
        p = safe_file(root, name)
        if p.resolve() == output.resolve():
            raise ValueError('Manifest cannot include itself')
        files[name] = fingerprint(p)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(dict(schema=1, files=files), stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return dict(passed=True, operation='seal', files=len(files), integrity_only=True)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def check(root, manifest):
    obj = json.loads(Path(manifest).read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(obj, dict) or type(obj.get('schema')) is not int or obj['schema'] != 1:
        raise ValueError('Unsupported manifest schema')
    if not isinstance(obj.get('files'), dict) or not obj['files']:
        raise ValueError('Missing file entries')
    errors = []
    for name, expected in obj['files'].items():
        if not isinstance(expected, dict) or type(expected.get('bytes')) is not int or expected['bytes'] < 0:
            raise ValueError('Invalid length')
        if not isinstance(expected.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', expected['sha256']):
            raise ValueError('Invalid SHA256')
        try:
            actual = fingerprint(safe_file(root, name))
        except FileNotFoundError:
            errors.append(dict(file=name, reason='missing'))
            continue
        if actual != expected:
            errors.append(dict(file=name, reason='changed'))
    return dict(passed=not errors, operation='check', files=len(obj['files']), errors=errors,
                integrity_only=True, unlisted_files_checked=False)


def read_csv(path):
    with Path(path).open(encoding='utf-8', newline='') as stream:
        rows = csv.reader(stream, strict=True)
        if next(rows, None) != ['id', 'value']:
            raise ValueError('CSV header must be id,value')
        values = {}
        for row in rows:
            if len(row) != 2 or not row[0].strip() or row[0] != row[0].strip() or row[0] in values:
                raise ValueError('CSV IDs must be nonempty and unique; exactly two columns')
            values[row[0]] = finite(row[1])
        if not values:
            raise ValueError('Empty CSV dataset')
        return values


def compare(expected, actual, atol='0'):
    tolerance = finite(atol)
    if tolerance < 0:
        raise ValueError('Tolerance must be nonnegative')
    reference, observed = read_csv(expected), read_csv(actual)
    missing = sorted(reference.keys()-observed.keys())
    extra = sorted(observed.keys()-reference.keys())
    mismatches = []
    for key in sorted(reference.keys() & observed.keys()):
        nums = (reference[key], observed[key], tolerance)
        high = max(n.adjusted() for n in nums)
        low = min(n.as_tuple().exponent for n in nums)
        with localcontext() as ctx:
            ctx.prec = max(28, high-low+3)
            error = abs(observed[key]-reference[key])
        if error > tolerance:
            mismatches.append(dict(id=key, expected=str(reference[key]), actual=str(observed[key]), abs_error=str(error)))
    return dict(passed=not(missing or extra or mismatches), operation='compare',
                expected_rows=len(reference), actual_rows=len(observed), absolute_tolerance=str(tolerance),
                missing=missing, extra=extra, mismatches=mismatches, numeric_agreement_only=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='operation', required=True)
    p = commands.add_parser('seal')
    p.add_argument('--root', required=True); p.add_argument('--file', action='append', required=True)
    p.add_argument('--out', required=True)
    p = commands.add_parser('check')
    p.add_argument('--root', required=True); p.add_argument('--manifest', required=True)
    p = commands.add_parser('compare')
    p.add_argument('--expected', required=True); p.add_argument('--actual', required=True)
    p.add_argument('--atol', default='0')
    a = ap.parse_args(argv)
    try:
        if a.operation == 'seal': result = seal(a.root, a.file, a.out)
        elif a.operation == 'check': result = check(a.root, a.manifest)
        else: result = compare(a.expected, a.actual, a.atol)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['passed'] else 1
    except (ValueError, OSError, csv.Error, OverflowError) as exc:
        print(json.dumps(dict(passed=False, operation=a.operation, input_error=type(exc).__name__,
                              message=str(exc)), ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
