import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from mvp5.models import Config
from mvp5.store import load_record, digest
from mvp5.workload_experiment import run_workload, workload_config

FIXTURES = (Path(__file__).resolve().parents[3] / 'fixtures/validation_20261011_b02')


def records():
    return [load_record(FIXTURES / f'raw_{i:04d}.tscap') for i in range(1, 7)]


class FixtureClient:
    """Replay fixed physical records to test host action safety, not generate evidence."""
    def __init__(self, captures, state='IDLE', previous=None):
        self.captures = captures
        self.previous = previous or captures[0]
        self.state, self.session_id = state, self.previous.metadata['session_id'] - 1
        self.config_id = captures[0].metadata['config_id']
        self.retries = 0
        self.decoder = SimpleNamespace(crc_errors=0)
        self.cancel = lambda: False
        self.uncertain = False
        self.index = -1
        self.actions = []

    def status(self):
        return dict(state=self.state, session_id=self.session_id, config_id=self.config_id)

    def get_config(self):
        return Config.from_dict(self.previous.metadata['config'])

    def configure(self, cfg):
        self.actions.append('configure')
        return dict(config_id=self.config_id, config=cfg.as_dict())

    def arm(self):
        self.actions.append('arm')
        self.index += 1
        self.session_id = self.captures[self.index].metadata['session_id']
        self.state = 'ARMED'

    def wait_frozen(self, timeout):
        self.state = 'FROZEN'

    def read_capture(self, progress=None):
        return copy.deepcopy(self.previous if self.index < 0 else self.captures[self.index])

    def release(self):
        self.actions.append('release')
        self.state = 'IDLE'
        return self.status()


class WorkloadExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / 'run'

    def tearDown(self):
        self.temp.cleanup()

    def test_full_run_saves_six_and_keeps_last_frozen(self):
        client = FixtureClient(records())
        result = run_workload(client, self.out)
        self.assertTrue(result['report']['passed'])
        self.assertEqual(client.actions.count('release'), 5)
        self.assertEqual(client.state, 'FROZEN')
        self.assertEqual([r['count'] for r in result['report']['groups']], [2, 2, 2])
        self.assertFalse(result['report']['mcu_internal_result_checked'])
        for case in result['report']['cases']:
            self.assertEqual(digest((self.out / case['file']).read_bytes()), case['sha256'])
        self.assertTrue((self.out / 'summary.html').is_file())

    def test_arbitrary_start_id_still_balances_by_real_markers(self):
        caps = [load_record(FIXTURES / f'raw_{i:04d}.tscap') for i in range(2, 8)]
        result = run_workload(FixtureClient(caps), self.out)
        self.assertEqual([c['identity']['id'] for c in result['report']['cases']], list(range(2, 8)))
        self.assertEqual([g['count'] for g in result['report']['groups']], [2, 2, 2])

    def test_existing_frozen_record_saved_before_release(self):
        caps = records()
        client = FixtureClient(caps, state='FROZEN')
        previous_release = client.release
        def checked_release():
            if not client.actions:
                saved = load_record(self.out / 'protected_previous.tscap')
                self.assertEqual(saved.samples, caps[0].samples)
                self.assertEqual(saved.events, caps[0].events)
            return previous_release()
        client.release = checked_release
        result = run_workload(client, self.out)
        self.assertIsNotNone(result['report']['protected_previous'])
        self.assertEqual(client.actions.count('release'), 6)

    def test_partial_previous_capture_is_saved_without_any_release_or_arm(self):
        cap = records()[0]
        client = FixtureClient(records(), state='FROZEN', previous=replace(cap, samples=cap.samples[:41]))
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertEqual(client.actions, [])
        self.assertTrue((self.out / 'protected_previous.tscap').exists())

    def test_partial_new_capture_saved_and_never_released(self):
        caps = records(); caps[0] = replace(caps[0], samples=caps[0].samples[:41])
        client = FixtureClient(caps)
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertEqual(client.actions, ['configure', 'arm'])
        report = json.loads((self.out / 'experiment.json').read_bytes())
        self.assertFalse(report['passed'])
        self.assertFalse(load_record(self.out / report['latest_record']).complete)

    def test_disk_save_failure_never_releases_current_capture(self):
        client = FixtureClient(records())
        with patch('mvp5.workload_experiment.save_record', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                run_workload(client, self.out)
        self.assertEqual(client.actions, ['configure', 'arm'])

    def test_bad_storage_stops_before_status_or_any_board_mutation(self):
        client = FixtureClient(records(), state='FROZEN')
        with patch.object(client, 'status', side_effect=AssertionError('No device access')) as status, \
             patch('mvp5.workload_experiment.probe_directory', side_effect=OSError('atomic replace failed')):
            with self.assertRaises(OSError):
                run_workload(client, self.out)
        status.assert_not_called()
        self.assertEqual(client.actions, [])

    def test_both_writes_fail_preserves_original_error_and_memory_capture(self):
        client = FixtureClient(records())
        received = []
        with patch('mvp5.workload_experiment.save_record', side_effect=OSError('primary record error')), \
             patch('mvp5.workload_experiment.publish', side_effect=PermissionError('secondary report error')):
            with self.assertRaisesRegex(OSError, 'primary record error') as error:
                run_workload(client, self.out, preserve=lambda cap, path: received.append((cap, path)))
        self.assertEqual(client.actions, ['configure', 'arm'])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0].samples, client.captures[0].samples)
        self.assertIsNone(received[0][1])
        self.assertIn('secondary report error', error.exception.__notes__[0])

    def test_failed_save_of_existing_frozen_record_never_releases_it(self):
        client = FixtureClient(records(), state='FROZEN')
        received = []
        with patch('mvp5.workload_experiment.save_record', side_effect=OSError('previous save failed')):
            with self.assertRaises(OSError):
                run_workload(client, self.out, preserve=lambda cap, path: received.append((cap, path)))
        self.assertEqual(client.actions, [])
        self.assertEqual(received, [(client.previous, None)])

    def test_repeated_job_stops_before_second_release(self):
        caps = records(); caps[1] = copy.deepcopy(caps[0])
        caps[1].metadata['session_id'] += 1
        client = FixtureClient(caps)
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertEqual(client.actions.count('arm'), 2)
        self.assertEqual(client.actions.count('release'), 1)
        self.assertTrue((self.out / 'round_02_s1107.tscap').exists())

    def test_protocol_retry_preserves_first_new_record(self):
        client = FixtureClient(records())
        original = client.read_capture
        def with_retry(progress=None):
            cap = original(progress)
            client.retries += 1
            return cap
        client.read_capture = with_retry
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertNotIn('release', client.actions)

    def test_cancel_after_saved_capture_does_not_release_it(self):
        client = FixtureClient(records())
        client.cancel = lambda: client.index >= 0
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertNotIn('release', client.actions)
        report = json.loads((self.out / 'experiment.json').read_bytes())
        self.assertTrue((self.out / report['latest_record']).exists())

    def test_active_state_does_not_arm_configure_or_release(self):
        client = FixtureClient(records(), state='ARMED')
        with self.assertRaises(ValueError):
            run_workload(client, self.out)
        self.assertEqual(client.actions, [])

    def test_supported_presets_and_wire_roundtrip(self):
        for deadline in (3.5, 8):
            cfg = workload_config(deadline)
            self.assertEqual(cfg.to_bytes(), Config.from_bytes(cfg.to_bytes()).to_bytes())
            self.assertEqual((cfg.enabled_mask, cfg.divisor, cfg.pre, cfg.post), (5, 10, 45000, 20535))
        for bad in (True, 2, '3.5'):
            with self.assertRaises(ValueError):
                workload_config(bad)


class WorkloadRunnerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_offline_preset_does_not_mutate_record_or_connect(self):
        from mvp5.ui import MainWindow
        window = MainWindow(); window.timer.stop()
        try:
            window.capture = records()[0]
            window.saved = window.replay = True
            before = copy.deepcopy(window.capture)
            for index in (0, 1):
                window.workload_deadline.setCurrentIndex(index)
                window.workload_preset.click()
                self.assertEqual(window.read_draft().to_bytes(), workload_config((3.5, 8)[index]).to_bytes())
                self.assertEqual(window.capture, before)
                self.assertIsNone(window.client)
                self.assertFalse(window.workload_run.isEnabled())
        finally:
            window.capture = None; window.close(); window.deleteLater(); self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
