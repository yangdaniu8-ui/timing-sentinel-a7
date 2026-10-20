import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.capture_storage import (initial_root, probe_directory,
    read_preference, write_preference)


class CaptureStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_probe_checks_new_publication_and_replacement_and_leaves_no_files(self):
        folder = self.root / 'records'
        self.assertEqual(probe_directory(folder), folder.resolve())
        original = folder / 'existing.tscap'
        original.write_bytes(b'keep user evidence')
        probe_directory(folder)
        self.assertEqual(list(folder.iterdir()), [original])
        self.assertEqual(original.read_bytes(), b'keep user evidence')

    def test_atomic_replace_failure_is_not_hidden_by_successful_plain_write(self):
        with patch('app.services.record_store.os.replace', side_effect=OSError(17, 'injected same-device error')):
            with self.assertRaises(OSError):
                probe_directory(self.root / 'records')
        self.assertEqual(list((self.root / 'records').iterdir()), [])

    def test_failure_only_when_replacing_existing_probe_is_detected(self):
        import os
        original = os.replace
        def replace(source, destination):
            if Path(destination).exists():
                raise PermissionError('injected replacement denied')
            original(source, destination)
        with patch('app.services.record_store.os.replace', side_effect=replace):
            with self.assertRaises(PermissionError):
                probe_directory(self.root / 'records')
        self.assertEqual(list((self.root / 'records').iterdir()), [])

    def test_file_instead_of_directory_is_rejected_without_changes(self):
        path = self.root / 'file'
        path.write_bytes(b'unchanged')
        with self.assertRaises(OSError):
            probe_directory(path)
        self.assertEqual(path.read_bytes(), b'unchanged')

    def test_preferences_roundtrip_and_explicit_root_priority(self):
        settings = self.root / 'prefs.json'
        self.assertIsNone(read_preference(settings))
        selected = self.root / '中文目录'
        write_preference(selected, settings)
        self.assertEqual(read_preference(settings), selected.resolve())
        self.assertEqual(initial_root(None, self.root, settings), (selected.resolve(), None))
        self.assertEqual(initial_root(self.root / 'explicit', self.root, settings),
                         (self.root / 'explicit', None))

    def test_invalid_preference_warns_and_falls_back_without_rewriting(self):
        settings = self.root / 'prefs.json'
        for content in ('{', '[]', json.dumps(dict(schema=1, capture_root='relative/path')),
                        json.dumps(dict(schema=2, capture_root=str(self.root))),
                        json.dumps(dict(schema=True, capture_root=str(self.root))), 'x' * 16385):
            settings.write_text(content, encoding='utf-8')
            root, warning = initial_root(None, self.root, settings)
            self.assertEqual(root, self.root)
            self.assertTrue(warning)
            self.assertEqual(settings.read_text(encoding='utf-8'), content)

    def test_preference_publish_failure_keeps_old_settings(self):
        settings = self.root / 'prefs.json'
        write_preference(self.root / 'old', settings)
        original = settings.read_bytes()
        with patch('app.services.record_store.os.replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                write_preference(self.root / 'new', settings)
        self.assertEqual(settings.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
