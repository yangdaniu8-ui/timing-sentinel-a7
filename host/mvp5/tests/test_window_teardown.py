"""Accepted close stops polling; blocked close must preserve the live window."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication


class WindowTeardownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp = TemporaryDirectory()
        self.guard = patch('mvp5.ui.SerialTransport', side_effect=AssertionError('No hardware in teardown test'))
        self.guard.start()
        self.window = MainWindow(root=Path(self.temp.name))
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.busy = False
        self.window.saved = True
        self.window.capture = self.window.client = self.window.transport = None
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.guard.stop()
        self.temp.cleanup()

    def test_accepted_close_stops_polling_and_is_repeatable(self):
        self.assertTrue(self.window.timer.isActive())
        first = QCloseEvent()
        self.window.closeEvent(first)
        self.assertTrue(first.isAccepted())
        self.assertFalse(self.window.timer.isActive())
        second = QCloseEvent()
        self.window.closeEvent(second)
        self.assertTrue(second.isAccepted())
        self.assertFalse(self.window.timer.isActive())

    def test_busy_close_keeps_timer_and_plot_available(self):
        self.window.busy = True
        with patch('mvp5.ui.QMessageBox.information'), patch.object(self.window.wave.plot, 'close') as close:
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(self.window.timer.isActive())
            close.assert_not_called()

    def test_cancelled_unsaved_close_keeps_evidence_and_plot(self):
        from mvp5.tests.test_mvp4 import fixture
        cap = fixture()
        self.window.capture = cap
        self.window.saved = self.window.replay = False
        with patch('mvp5.ui.QMessageBox.question', return_value=ui_no()), patch.object(self.window.wave.plot, 'close') as close:
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertIs(self.window.capture, cap)
            self.assertFalse(self.window.saved)
            self.assertTrue(self.window.timer.isActive())
            close.assert_not_called()


def ui_no():
    from PySide6.QtWidgets import QMessageBox
    return QMessageBox.StandardButton.No


if __name__ == '__main__':
    unittest.main()
