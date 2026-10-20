import copy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from mvp5.models import unpack_events
from mvp5.presentation import identify_scene
from mvp5.store import load_record, digest
from mvp5.workload import parse_packet, workload_label

DATA = (Path(__file__).resolve().parents[3] / 'fixtures/validation_20261011_b02')
PILOT = DATA.parent / "pilot_20261011_b01"


def raw(job=1):
    return load_record(DATA / f"raw_{job:04d}.tscap")


def change_uart(cap, values):
    buf = bytearray(cap.events)
    index = 0
    for i, event in enumerate(unpack_events(cap.events)):
        if event["uart"] is not None:
            word = int.from_bytes(buf[i*16:i*16+16], "little")
            word = (word & ~(255 << 73)) | (values[index] << 73)
            buf[i*16:i*16+16] = word.to_bytes(16, "little")
            index += 1
    return replace(cap, events=bytes(buf))


class WorkloadTests(unittest.TestCase):
    def test_all_30_raw_and_30_annotated_immutable(self):
        files = sorted(DATA.glob("raw_*.tscap")) + sorted(DATA.glob("load_*/job_*.tscap"))
        self.assertEqual(len(files), 60)
        for path in files:
            cap = load_record(path); before = copy.deepcopy(cap); sha = digest(path.read_bytes())
            info = identify_scene(cap)
            self.assertTrue(info["verified"], (path, info))
            self.assertEqual(info["workload"]["id"], int(path.stem.split("_")[-1]))
            self.assertEqual(cap, before); self.assertEqual(digest(path.read_bytes()), sha)

    def test_heavy_pass_in_pilot_not_guessed_from_verdict(self):
        cap = load_record(PILOT / "raw_0003.tscap")
        self.assertEqual(cap.metadata["rules"][0]["result"], "PASS")
        self.assertEqual(identify_scene(cap)["workload"]["profile"], 2)
        self.assertTrue(identify_scene(cap)["verified"])

    def test_heavy_timeout_is_verified_diagnosis(self):
        cap = raw(3)
        self.assertEqual(cap.metadata["rules"][0]["result"], "TIMEOUT")
        self.assertTrue(identify_scene(cap)["verified"])

    def test_raw_without_annotation_identifies(self):
        cap = raw(2)
        self.assertNotIn("workload_experiment", cap.metadata)
        self.assertEqual(identify_scene(cap)["name"], "轻度中断竞争")

    def test_untrusted_annotation_never_supplies_identity(self):
        cap = copy.deepcopy(raw(3))
        cap.metadata["workload_experiment"] = {"marker": {"id": 999, "profile": 0}}
        self.assertEqual(identify_scene(cap)["workload"]["id"], 3)
        self.assertEqual(identify_scene(cap)["workload"]["profile"], 2)

    def test_no_events_not_guessed_from_annotation(self):
        cap = copy.deepcopy(raw())
        cap.metadata["workload_experiment"] = {"marker": {"id": 1, "profile": 0}}
        self.assertIsNone(identify_scene(replace(cap, events=b""))["scene"])
        self.assertFalse(identify_scene(replace(cap, events=b""))["verified"])

    def test_invalid_packet_fields_rejected(self):
        good = bytes.fromhex("e24040416052557b72a5")
        for i, value in ((0,0xe1),(1,0x50),(2,0x60),(3,0x30),(4,0x63),
                         (5,0x40),(6,0x60),(7,0x60),(8,0x73),(9,0xa4)):
            bad = bytearray(good); bad[i] = value
            with self.subTest(i=i), self.assertRaises(ValueError): parse_packet(bad)
        for bad in (good[:-1], good+good, bytes.fromhex("e24040406052557b73a5")):
            with self.assertRaises(ValueError): parse_packet(bad)

    def test_number_max_and_non_scheduled_profile_supported(self):
        values = [0xe2,0x4f,0x4f,0x4f,0x60,0x5f,0x5f,0x79,0x79,0xa5]
        # Identity is the packet, not (id-1)%6 scheduling or id*37 phase.
        check = (4095 ^ (4095>>8) ^ 0 ^ 255 ^ 0x96) & 255
        values[7:9] = [0x70|(check>>4),0x70|(check&15)]
        self.assertEqual(parse_packet(values)["id"],4095)
        self.assertEqual(parse_packet(values)["profile"],0)

    def test_event_checksum_corruption_not_identified(self):
        cap = raw(); values = [e["uart"] for e in unpack_events(cap.events) if e["uart"] is not None]
        values[8] ^= 1
        info = identify_scene(change_uart(cap,values))
        self.assertIsNone(info["workload"]); self.assertFalse(info["verified"])

    def test_valid_event_packet_conflicts_with_samples(self):
        cap = raw()
        info = identify_scene(change_uart(cap,bytes.fromhex("e240404261545a7d7fa5")))
        self.assertEqual(info["workload"]["id"],2); self.assertFalse(info["verified"])
        self.assertIn("不一致",info["detail"])

    def test_partial_identity_not_verified(self):
        info = identify_scene(replace(raw(),samples=raw().samples[:41]))
        self.assertEqual(info["workload"]["id"],1); self.assertFalse(info["verified"])
        self.assertIn("不完整",info["status"])

    def test_mock_never_hardware_verified(self):
        cap = copy.deepcopy(raw()); cap.metadata["origin"] = "mock"
        info = identify_scene(cap)
        self.assertFalse(info["verified"]); self.assertIn("模拟",info["status"])

    def test_overwrite_and_multiple_a_rejected(self):
        for field in ("overwrite","accepted","a"):
            cap = copy.deepcopy(raw())
            if field == "overwrite": cap.metadata["event_overwrites"] = 1
            elif field == "accepted": cap.metadata["rules"][0]["counts"]["accepted"] = 2
            else: cap.metadata["rules"][0]["a_tick"] += 1
            self.assertFalse(identify_scene(cap)["verified"])

    def test_corrupted_samples_never_verified(self):
        cap = raw()
        self.assertFalse(identify_scene(replace(cap,samples=bytes([0x11])*len(cap.samples)))["verified"])

    def test_label_nominal_not_actual_irq_claim(self):
        text = workload_label(identify_scene(raw(3))["workload"])
        for term in ("重度","#3","标称 8000 Hz","相位 111"): self.assertIn(term,text)


class WorkloadGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_all_profiles_visible_and_stale_status_preserves_identity(self):
        from mvp5.ui import MainWindow
        w = MainWindow(); w.timer.stop(); w.replay = w.saved = True
        try:
            for job in (1,2,3):
                cap = raw(job); w.show_capture(cap)
                self.assertIn(f"任务 #{job}",w.scene_banner.text())
                self.assertIn("相位",w.scene_banner.text())
                self.assertTrue(w.scene_info["verified"])
                before = w.scene_banner.text()
                w.set_status(dict(state="FROZEN",session_id=99999,config_id=99))
                self.assertEqual(before,w.scene_banner.text())
            w.client = Mock(); w.client.uncertain = False; w.device_state = "FROZEN"
            w.show_capture(raw(2)); self.assertEqual(w.client.method_calls,[])
        finally:
            w.capture = w.client = None; w.close(); w.deleteLater(); self.app.processEvents()


if __name__ == "__main__": unittest.main()
