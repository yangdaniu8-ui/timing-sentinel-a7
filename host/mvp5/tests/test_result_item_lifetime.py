"""Result rendering must reuse Qt-owned items and never mutate evidence."""
import copy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from mvp5.store import load_record
from mvp5.tests.test_mvp4 import fixture

FIXTURES = (Path(__file__).resolve().parents[3] / 'fixtures/usr43')


class ResultItemLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp = TemporaryDirectory()
        self.serial_guard = patch('mvp5.ui.SerialTransport', side_effect=AssertionError('No real serial'))
        self.serial_guard.start()
        self.window = MainWindow(root=self.temp.name)
        self.window.timer.stop()
        self.window.saved = self.window.replay = True

    def tearDown(self):
        self.window.capture = self.window.client = self.window.transport = None
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.serial_guard.stop()
        self.temp.cleanup()

    def cells(self):
        return [[self.window.result_table.item(row, col) for col in range(8)] for row in range(4)]

    def test_refresh_reuses_every_existing_item(self):
        cap = fixture()
        self.window.show_capture(cap)
        before = self.cells()
        self.window.show_capture(cap)
        for row in range(4):
            for col in range(8):
                self.assertIs(self.window.result_table.item(row, col), before[row][col])

    def test_different_results_update_text_and_colors_without_replacing_cells(self):
        wide = load_record(next(FIXTURES.glob('wide_load2_*')))
        narrow = load_record(next(FIXTURES.glob('narrow_load2_*')))
        self.window.show_capture(wide)
        cells = self.cells()
        self.assertEqual(cells[0][1].text(), 'PASS')
        self.window.show_capture(narrow)
        self.assertIs(self.window.result_table.item(0, 1), cells[0][1])
        self.assertEqual(cells[0][1].text(), 'TIMEOUT')
        self.assertEqual(cells[0][1].foreground().color().name(), '#ff9b9b')
        self.window.show_capture(wide)
        self.assertEqual(cells[0][1].text(), 'PASS')
        self.assertEqual(cells[0][1].foreground().color().name(), '#91e7b1')

    def test_capture_reset_blanks_cells_but_does_not_destroy_items(self):
        self.window.show_capture(fixture())
        cells = self.cells()
        # UI completion only: do not invoke client.arm or start a worker.
        self.window.start = lambda operation, done: done(dict(state='ARMED', session_id=1, config_id=1))
        self.window.arm()
        self.assertIsNone(self.window.capture)
        for row in range(4):
            for col in range(8):
                self.assertIs(self.window.result_table.item(row, col), cells[row][col])
                self.assertEqual(cells[row][col].text(), '')

    def test_complete_partial_and_mock_renders_leave_raw_evidence_unchanged(self):
        original = load_record(next(FIXTURES.glob('wide_load2_*')))
        before = copy.deepcopy(original)
        for cap in (original, replace(original, samples=original.samples[:41]), fixture(), original):
            self.window.show_capture(cap)
        self.assertEqual(original, before)
        for row, entry in enumerate(original.metadata['rules']):
            self.assertEqual(self.window.result_table.item(row, 1).text(), entry['result'])
        self.assertIsNone(self.window.client)
        self.assertFalse(self.window.buttons['release'].isEnabled())

    def test_reference_refresh_keeps_header_and_time_cells_alive(self):
        cap = load_record(next(FIXTURES.glob('wide_load2_*')))
        self.window.show_capture(cap)
        header = self.window.event_table.horizontalHeaderItem(0)
        times = [self.window.event_table.item(row, 0) for row in range(self.window.event_table.rowCount())]
        self.window.refresh_event_times()
        self.assertIs(self.window.event_table.horizontalHeaderItem(0), header)
        for row, item in enumerate(times):
            self.assertIs(self.window.event_table.item(row, 0), item)

    def test_same_events_reuse_full_and_compact_table_items(self):
        cap = load_record(next(FIXTURES.glob('wide_load2_*')))
        self.window.show_capture(cap)
        tables = (self.window.event_table, self.window.compact_events)
        originals = [[[table.item(row, col) for col in range(table.columnCount())]
                      for row in range(table.rowCount())] for table in tables]
        self.window.populate_events()
        for table, rows in zip(tables, originals):
            for row, cells in enumerate(rows):
                for col, item in enumerate(cells):
                    self.assertIs(table.item(row, col), item)


if __name__ == '__main__':
    unittest.main()
