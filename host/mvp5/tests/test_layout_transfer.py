"""Transferred controls survive; borrowed layout-item wrappers must not survive."""
import unittest
import shiboken6
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QPushButton
from mvp5.compact_layout import reparent_widget,add_widget


class LayoutTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.old=QWidget();self.new=QWidget()
        self.layout=QVBoxLayout(self.old)
        self.button=QPushButton('全览');self.layout.addWidget(self.button)

    def tearDown(self):
        self.old.deleteLater();self.new.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()

    def test_old_layout_item_invalidated_but_button_and_click_preserved(self):
        item=self.layout.itemAt(0);clicks=[]
        self.button.clicked.connect(lambda:clicks.append(True))
        reparent_widget(self.button,self.new)
        self.assertFalse(shiboken6.isValid(item))
        self.assertTrue(shiboken6.isValid(self.button))
        self.assertIs(self.button.parentWidget(),self.new)
        self.assertEqual(self.layout.count(),0)
        self.button.click();self.assertEqual(clicks,[True])

    def test_nested_layout_removes_only_the_target(self):
        nested=QHBoxLayout();self.layout.addLayout(nested)
        other=QPushButton('other');nested.addWidget(other)
        item=nested.itemAt(0)
        reparent_widget(other,self.new)
        self.assertFalse(shiboken6.isValid(item));self.assertEqual(nested.count(),0)
        self.assertEqual(self.layout.count(),2)
        self.assertIs(self.button.parentWidget(),self.old)

    def test_same_parent_does_not_remove_existing_layout_item(self):
        item=self.layout.itemAt(0)
        reparent_widget(self.button,self.old)
        self.assertTrue(shiboken6.isValid(item));self.assertEqual(self.layout.count(),1)

    def test_no_previous_layout_is_safe(self):
        free=QPushButton('free',self.new)
        reparent_widget(free,self.old)
        self.assertIs(free.parentWidget(),self.old)
        self.assertEqual(self.layout.count(),1)

    def test_add_widget_moves_between_layouts_of_same_parent_safely(self):
        nested=QHBoxLayout();self.layout.addLayout(nested)
        item=self.layout.itemAt(0)
        add_widget(nested,self.button)
        self.assertFalse(shiboken6.isValid(item))
        self.assertIs(self.button.parentWidget(),self.old)
        self.assertEqual(nested.count(),1)

    def test_add_widget_moves_to_new_parent_without_stale_item(self):
        dest=QVBoxLayout(self.new);item=self.layout.itemAt(0)
        add_widget(dest,self.button,1)
        self.assertFalse(shiboken6.isValid(item));self.assertEqual(self.layout.count(),0)
        self.assertEqual(dest.count(),1);self.assertIs(self.button.parentWidget(),self.new)


if __name__=='__main__':unittest.main()
