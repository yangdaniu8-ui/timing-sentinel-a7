"""Behavior tests create only temporary synthetic fixtures; no hardware access."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import stat

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_check as ec

HERE = Path(__file__).resolve().parents[1]


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = self.root/'a.csv'; self.b = self.root/'b.csv'
        self.manifest = self.root/'baseline.json'
        self.a.write_text('id,value\na,1\nb,2\n', encoding='utf-8')
        self.b.write_bytes(self.a.read_bytes())

    def cli(self, args, want):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): code = ec.main(args)
        result = json.loads(output.getvalue())
        self.assertEqual(code, want, result)
        return result

    def baseline(self):
        ec.seal(self.root, ['a.csv'], self.manifest)

    def test_seal_and_verify(self):
        self.baseline(); self.assertTrue(ec.check(self.root, self.manifest)['passed'])

    def test_changed_same_length(self):
        self.baseline(); self.a.write_bytes(self.a.read_bytes().replace(b'a,1', b'a,9'))
        result = self.cli(['check', '--root', str(self.root), '--manifest', str(self.manifest)], 1)
        self.assertEqual(result['errors'][0]['reason'], 'changed')

    def test_missing(self):
        self.baseline(); self.a.unlink()
        self.assertEqual(ec.check(self.root, self.manifest)['errors'][0]['reason'], 'missing')

    def test_no_overwrite(self):
        self.baseline(); old = self.manifest.read_bytes()
        with self.assertRaises(FileExistsError): self.baseline()
        self.assertEqual(old, self.manifest.read_bytes())

    def test_empty_selection(self):
        with self.assertRaises(ValueError): ec.seal(self.root, [], self.manifest)

    def test_duplicate_selection(self):
        with self.assertRaises(ValueError): ec.seal(self.root, ['a.csv', 'a.csv'], self.manifest)

    def test_unsafe_paths(self):
        for name in ('../a.csv', '/a.csv', 'D:/a.csv', 'a\\b', './a.csv', 'a//b', 'a/../b'):
            with self.subTest(name=name), self.assertRaises(ValueError): ec.safe_file(self.root, name)

    def test_directory_rejected(self):
        (self.root/'dir').mkdir()
        with self.assertRaises(ValueError): ec.safe_file(self.root, 'dir')

    def test_link_rejected(self):
        link = self.root/'link.csv'
        try: link.symlink_to(self.a)
        except OSError: self.skipTest('OS does not permit creating symbolic links')
        with self.assertRaises(ValueError): ec.safe_file(self.root, 'link.csv')

    def test_link_mode_guard_without_os_privilege(self):
        original = type(self.a).lstat
        canonical = self.a.resolve()
        def lstat(p, *args, **kwargs):
            return SimpleNamespace(st_mode=stat.S_IFLNK) if p == canonical else original(p, *args, **kwargs)
        with patch.object(type(self.a), 'lstat', lstat), self.assertRaises(ValueError): ec.safe_file(self.root, 'a.csv')

    def test_reparse_guard_without_os_privilege(self):
        original = type(self.a).lstat
        canonical = self.a.resolve()
        def lstat(p, *args, **kwargs):
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400) if p == canonical else original(p, *args, **kwargs)
        with patch.object(type(self.a), 'lstat', lstat), self.assertRaises(ValueError): ec.safe_file(self.root, 'a.csv')

    def test_empty_manifest(self):
        self.manifest.write_text('{"schema":1,"files":{}}')
        with self.assertRaises(ValueError): ec.check(self.root, self.manifest)

    def test_boolean_schema_rejected(self):
        self.manifest.write_text('{"schema":true,"files":{"a":{}}}')
        with self.assertRaises(ValueError): ec.check(self.root, self.manifest)

    def test_duplicate_json_rejected(self):
        self.manifest.write_text('{"schema":1,"schema":1,"files":{}}')
        with self.assertRaises(ValueError): ec.check(self.root, self.manifest)

    def test_invalid_digest(self):
        self.manifest.write_text(json.dumps(dict(schema=1, files={'a.csv': dict(bytes=4, sha256='bad')})))
        with self.assertRaises(ValueError): ec.check(self.root, self.manifest)

    def test_unlisted_files_are_outside_scope(self):
        self.baseline(); self.b.write_text('unlisted changed')
        result = ec.check(self.root, self.manifest)
        self.assertTrue(result['passed']); self.assertFalse(result['unlisted_files_checked'])

    def test_numeric_equal_reordered(self):
        self.b.write_text('id,value\nb,2.00\na,1e0\n')
        self.assertTrue(ec.compare(self.a, self.b)['passed'])

    def test_numeric_failure(self):
        self.b.write_text('id,value\na,1\nb,3\n')
        result = self.cli(['compare', '--expected', str(self.a), '--actual', str(self.b)], 1)
        self.assertEqual(result['mismatches'][0]['id'], 'b')

    def test_missing_and_extra_ids(self):
        self.b.write_text('id,value\nc,1\nb,2\n')
        result = ec.compare(self.a, self.b)
        self.assertEqual(result['missing'], ['a']); self.assertEqual(result['extra'], ['c'])

    def test_duplicate_ids(self):
        self.b.write_text('id,value\na,1\na,2\n')
        with self.assertRaises(ValueError): ec.compare(self.a, self.b)

    def test_wrong_header(self):
        self.b.write_text('value,id\n1,a\n')
        self.cli(['compare', '--expected', str(self.a), '--actual', str(self.b)], 2)

    def test_missing_value(self):
        self.b.write_text('id,value\na\n')
        with self.assertRaises(ValueError): ec.compare(self.a, self.b)

    def test_empty_dataset(self):
        self.b.write_text('id,value\n')
        with self.assertRaises(ValueError): ec.compare(self.a, self.b)

    def test_empty_or_padded_id(self):
        for key in ('', ' ', ' a'):
            self.b.write_text('id,value\n'+key+',1\n')
            with self.subTest(key=key), self.assertRaises(ValueError): ec.compare(self.a, self.b)

    def test_nonfinite(self):
        for value in ('NaN', 'Infinity', '-Infinity', 'sNaN'):
            with self.subTest(value=value), self.assertRaises(ValueError): ec.finite(value)

    def test_bounded_decimal_size(self):
        for value in ('1e999999', '1e-999999', '1'*4097):
            with self.subTest(value=value), self.assertRaises(ValueError): ec.finite(value)

    def test_bad_tolerance(self):
        for value in ('-1', 'NaN', 'wrong'):
            with self.subTest(value=value), self.assertRaises(ValueError): ec.compare(self.a, self.b, value)

    def test_tolerance_closed_boundary(self):
        self.b.write_text('id,value\na,1.01\nb,2\n')
        self.assertTrue(ec.compare(self.a, self.b, '.01')['passed'])
        self.assertFalse(ec.compare(self.a, self.b, '.009')['passed'])

    def test_long_decimal_not_rounded_to_pass(self):
        self.a.write_text('id,value\na,1\n')
        self.b.write_text('id,value\na,1.00000000000000000000000000000000000000001\n')
        self.assertFalse(ec.compare(self.a, self.b)['passed'])

    def test_counter_fixture(self):
        self.assertTrue(ec.compare(HERE/'examples/counter_expected.csv', HERE/'examples/counter_actual.csv')['passed'])

    def test_fir_fixture_from_definition(self):
        data = [1, 2, 3, 4]
        expected = ec.read_csv(HERE/'examples/fir_expected.csv')
        self.assertEqual([expected[f'y{i}'] for i in range(4)], [sum(data[max(0,i-3):i+1]) for i in range(4)])
        self.assertTrue(ec.compare(HERE/'examples/fir_expected.csv', HERE/'examples/fir_actual.csv')['passed'])
        bad = ec.compare(HERE/'examples/fir_expected.csv', HERE/'examples/fir_bad.csv')
        self.assertEqual(bad['mismatches'][0]['id'], 'y3')

    def test_relocated_standalone_cli(self):
        moved = self.root/'unrelated_counter_project'; moved.mkdir()
        script = moved/'checker.py'; shutil.copy2(HERE/'scripts/evidence_check.py', script)
        for name in ('counter_expected.csv', 'counter_actual.csv'):
            shutil.copy2(HERE/'examples'/name, moved/name)
        proc = subprocess.run([sys.executable, '-I', '-B', str(script), 'compare',
                               '--expected', 'counter_expected.csv', '--actual', 'counter_actual.csv'],
                              cwd=moved, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['passed'])

    def test_cli_success(self):
        self.assertTrue(self.cli(['compare', '--expected', str(self.a), '--actual', str(self.b)], 0)['passed'])

    def test_missing_input_exit_two(self):
        self.cli(['compare', '--expected', str(self.root/'absent.csv'), '--actual', str(self.b)], 2)


if __name__ == '__main__': unittest.main(verbosity=2)
