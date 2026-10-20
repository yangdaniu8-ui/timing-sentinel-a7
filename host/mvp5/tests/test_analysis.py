import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QAbstractItemView
from app.models import TICK_MASK, delta_tick
from mvp5.analysis import (audit_capture, analyze_records, delay_statistics, export_analysis,
                           render_html, MAX_FILES)
from mvp5.experiment import decode_uart
from mvp5.models import Config, unpack_events
from mvp5.store import load_record, save_record, digest

RECORDS = (Path(__file__).resolve().parents[3] / 'fixtures/hardware_six')
FILES = sorted(RECORDS.glob('*.tscap'))


def cap(index=0):
    return load_record(FILES[index])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, record, name='capture.tscap'):
        path = self.root/name
        save_record(record, path)
        return path

    def test_six_actual_raw_records_reconstructed(self):
        for index, path in enumerate(FILES):
            original = path.read_bytes()
            result = audit_capture(load_record(path))
            self.assertEqual(result['status'], 'verified', result)
            self.assertEqual(len(result['rules']), 4)
            self.assertEqual(original, path.read_bytes())
        self.assertEqual(len(FILES), 6)

    def test_no_scene_marker_needed(self):
        record = cap(); m = record.metadata
        frames = decode_uart(bytes(v & 1 for v in record.samples), m['first_sample_timestamp'], 5, 434)
        marker = frames[0]['nominal_stop_center']
        raw = bytearray(record.samples)
        for j in range(len(raw)):
            dt = delta_tick((m['first_sample_timestamp']+j*5)&TICK_MASK, marker)
            if -10*434 < dt < 434//2: raw[j] |= 1
        events = bytearray(record.events)
        for i, e in enumerate(unpack_events(record.events)):
            if e['uart'] == 0xd0:
                word = int.from_bytes(events[i*16:i*16+16], 'little')
                word &= ~((1<<72) | (255<<73))
                events[i*16:i*16+16] = word.to_bytes(16, 'little')
        self.assertEqual(audit_capture(replace(record, samples=bytes(raw), events=bytes(events)))['status'], 'verified')

    def test_partial_not_statistical_sample(self):
        result = analyze_records([self.write(replace(cap(), samples=cap().samples[:41]))])
        self.assertEqual(result['source_status_counts'], {'excluded': 1})
        self.assertEqual(result['groups'][0]['rules'], [])

    def test_mock_not_hardware_verified(self):
        record = cap(); record.metadata.update(origin='mock', firmware='mock-test')
        self.assertEqual(audit_capture(record)['status'], 'excluded')

    def test_manual_stop_excluded(self):
        record = cap(); record.metadata['manual_stop'] = True
        self.assertEqual(audit_capture(record)['status'], 'excluded')

    def test_event_overwrite_excluded(self):
        record = cap(); record.metadata['event_overwrites'] = 1
        self.assertEqual(audit_capture(record)['status'], 'excluded')

    def test_multiple_transactions_excluded(self):
        record = cap(); record.metadata['rules'][0]['counts']['accepted'] = 2
        self.assertEqual(audit_capture(record)['status'], 'excluded')

    def test_overlap_excluded(self):
        record = cap(); record.metadata['rules'][0]['counts']['overlap'] = 1
        self.assertEqual(audit_capture(record)['status'], 'excluded')

    def test_duplicate_files_count_once(self):
        report = analyze_records([FILES[0], FILES[0]])
        self.assertEqual(report['verified_records'], 1)
        self.assertEqual(report['source_status_counts'], {'verified':1, 'duplicate':1})

    def test_reread_new_uuid_version_retry_not_new_capture(self):
        record = cap(); original = self.write(record, 'a.tscap')
        record.metadata.update(record_id='00000000-0000-4000-8000-000000000001', app_version='0.9', transport_retries=1)
        report = analyze_records([original, self.write(record, 'b.tscap')])
        self.assertEqual(report['unique_capture_contents'], 1)
        self.assertEqual(report['verified_records'], 1)
        self.assertEqual(len(report['saved_transport_warning_snapshots']), 1)

    def test_missing_not_zero_delay(self):
        rows = audit_capture(cap(3))['rules']
        self.assertTrue(all(row['observed_delay_ms'] is None for row in rows))
        report = analyze_records([FILES[3]])
        self.assertEqual(report['groups'][0]['rules'][0]['observed_response_delay']['n'], 0)
        self.assertIsNone(report['groups'][0]['rules'][0]['observed_response_delay']['mean_ms'])

    def test_late_observed_separate_from_matched(self):
        row = audit_capture(cap(2))['rules'][0]
        self.assertEqual(row['result'], 'TIMEOUT')
        self.assertIsNone(row['matched_delay_ms'])
        self.assertGreater(row['observed_delay_ms'], 2)

    def test_config_grouping_never_merges_different_windows(self):
        record = cap(); cfg = Config.from_dict(record.metadata['config'])
        rules = list(cfg.rules); rules[0] = replace(rules[0], min_ticks=10000)
        record.metadata['config'] = replace(cfg, rules=tuple(rules)).as_dict()
        report = analyze_records([FILES[0], self.write(record)])
        self.assertEqual(len(report['groups']), 2)
        self.assertEqual(report['verified_records'], 2)

    def test_invalid_input_retained_others_continue(self):
        path = self.root/'bad.tscap'; path.write_bytes(b'not zip')
        report = analyze_records([path, FILES[0]])
        self.assertEqual(report['source_status_counts'], {'invalid':1, 'verified':1})
        self.assertEqual(report['sources'][0]['file_sha256'], digest(b'not zip'))

    def test_file_extension_rejected(self):
        report = analyze_records([self.root/'bad.exe'])
        self.assertEqual(report['source_status_counts'], {'invalid':1})

    def test_cancellation_and_selection_limits(self):
        for paths in ([], [FILES[0]]*(MAX_FILES+1)):
            with self.assertRaises(ValueError): analyze_records(paths)
        with self.assertRaises(InterruptedError): analyze_records([FILES[0]], cancel=lambda:True)

    def test_bound_read_rejects_oversized_record(self):
        path = self.root/'huge.tscap'; path.write_bytes(bytes(2_000_001))
        self.assertEqual(analyze_records([path])['source_status_counts'], {'invalid':1})

    def test_wrong_gpio_samples_fail(self):
        record = cap()
        record = replace(record, samples=bytes(v & ~2 for v in record.samples))
        result = audit_capture(record)
        self.assertEqual(result['status'], 'mismatch', result)

    def test_wrong_spi_samples_fail(self):
        record = cap()
        self.assertEqual(audit_capture(replace(record, samples=bytes(v | 16 for v in record.samples)))['status'], 'mismatch')

    def test_a_missing_not_inferred_from_meta(self):
        record = cap()
        self.assertEqual(audit_capture(replace(record, samples=bytes(v | 1 for v in record.samples)))['status'], 'unverifiable')

    def test_quantization_boundary_not_forced_pass(self):
        record = cap(); cfg = Config.from_dict(record.metadata['config'])
        delay = audit_capture(record)['rules'][0]['sampled_observed_ms']*50000
        rules = list(cfg.rules); rules[0] = replace(rules[0], min_ticks=round(delay))
        record.metadata['config'] = replace(cfg, rules=tuple(rules)).as_dict()
        self.assertEqual(audit_capture(record)['status'], 'ambiguous')

    def test_insufficient_tail_cannot_prove_timeout(self):
        record = cap(3); m = record.metadata
        # Complete shorter buffer: covers the R0 2ms deadline, not R2/R3 3ms.
        end = delta_tick(m['rules'][0]['a_tick'], m['first_sample_timestamp'])+110000
        length = end//m['sample_divisor']+1
        m['valid_count'] = length
        record = replace(record, samples=record.samples[:length])
        self.assertTrue(record.complete)
        result = audit_capture(record)
        self.assertEqual(result['status'], 'unverifiable', result)
        self.assertIn('观察窗不足', result['reasons'][0])

    def test_48_bit_wrap_still_verified(self):
        record = cap(); m = record.metadata
        shift = TICK_MASK-m['rules'][0]['a_tick']-1000
        for key in ('first_sample_timestamp', 'capture_anchor_timestamp', 'decision_timestamp'):
            m[key] = (m[key]+shift)&TICK_MASK
        for entry in m['rules']:
            for key in ('a_tick', 'b_tick', 'observed_b_tick', 'deadline_tick', 'result_tick'):
                if entry[key] is not None: entry[key] = (entry[key]+shift)&TICK_MASK
        raw = bytearray(record.events)
        for i, e in enumerate(unpack_events(record.events)):
            word = int.from_bytes(raw[i*16:i*16+16], 'little')
            word = (word & ~TICK_MASK) | ((e['tick']+shift)&TICK_MASK)
            raw[i*16:i*16+16] = word.to_bytes(16, 'little')
        self.assertEqual(audit_capture(replace(record, events=bytes(raw)))['status'], 'verified')

    def test_counter_mismatch_not_pass(self):
        record = cap(); record.metadata['rules'][0]['counts']['pass'] = 0
        self.assertEqual(audit_capture(record)['status'], 'mismatch')

    def test_failed_original_preserved_and_html_escaped(self):
        path = self.write(cap())
        original = b'{"passed":false,"error":"<script>alert(1)</script>"}'
        context = self.root/'experiment.json'; context.write_bytes(original)
        report = analyze_records([path])
        self.assertEqual(report['original_failed_experiment_contexts'], 1)
        output = render_html(report)
        self.assertIn('&lt;script&gt;', output); self.assertNotIn('<script>', output)
        self.assertIn(digest(original), output)
        self.assertEqual(context.read_bytes(), original)

    def test_stats_empty_constant_percentile_and_bins(self):
        self.assertEqual(delay_statistics([])['n'], 0)
        self.assertEqual(delay_statistics([2,2])['stddev_ms'], 0)
        result = delay_statistics([1,2,3,4])
        self.assertEqual(result['p50_ms'], 2.5)
        self.assertAlmostEqual(result['p95_ms'], 3.85)
        self.assertAlmostEqual(result['p99_ms'], 3.97)
        self.assertEqual(sum(b['count'] for b in result['histogram']), 4)

    def test_export_json_html_and_source_integrity(self):
        source = self.write(cap()); before = source.read_bytes(); report = analyze_records([source])
        for suffix in ('json','html'): export_analysis(report,self.root/('analysis.'+suffix))
        saved = json.loads((self.root/'analysis.json').read_bytes())
        self.assertEqual(saved['scope'], 'selected_records_only')
        self.assertFalse(saved['serial_opened']); self.assertEqual(saved['new_hardware_transactions'], 0)
        self.assertEqual(source.read_bytes(), before)

    def test_export_cannot_clobber_evidence_or_experiment(self):
        source = self.write(cap()); before = source.read_bytes(); report = analyze_records([source])
        for path in (source, self.root/'experiment.json', self.root/'report.txt'):
            with self.assertRaises(ValueError): export_analysis(report,path)
        self.assertEqual(source.read_bytes(), before)

    def test_cli_analysis_never_constructs_serial_or_gui(self):
        from release_spi import main
        path = self.root/'report.json'
        with patch('release_spi.SerialTransport', side_effect=AssertionError('serial forbidden')), patch('release_spi.gui', side_effect=AssertionError('gui forbidden')):
            self.assertEqual(main(['--analyze-records',str(FILES[0]),'--analysis-report',str(path)]),0)
        self.assertEqual(json.loads(path.read_bytes())['verified_records'],1)

    def test_cli_directory_and_guarded_hardware_flags(self):
        from release_spi import main
        self.write(cap()); path = self.root/'report.html'
        self.assertEqual(main(['--analyze-directory',str(self.root),'--analysis-report',str(path)]),0)
        with self.assertRaises(ValueError):
            main(['--analyze-directory',str(self.root),'--analysis-report',str(path),'--port','COM6'])
        with self.assertRaises(ValueError): main(['--analysis-report',str(path)])

    def test_output_generation_failure_preserves_old_file(self):
        report=analyze_records(FILES[:1]); path=self.root/'report.html'; path.write_bytes(b'old report')
        with patch('mvp5.analysis.render_html',side_effect=ValueError('render failure')):
            with self.assertRaises(ValueError): export_analysis(report,path)
        self.assertEqual(path.read_bytes(),b'old report')


class AnalysisGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.w = MainWindow(); self.w.timer.stop()
        self.temp = tempfile.TemporaryDirectory()
        self.w.capture = cap(); self.w.replay = False; self.w.saved = False
        self.w.device_state = 'FROZEN'
        self.w.client = Mock(); self.w.client.uncertain = False
        self.w.show_capture(self.w.capture)
        self.before = (copy.deepcopy(self.w.capture), self.w.saved, self.w.replay, self.w.device_state)

    def tearDown(self):
        self.w.capture = self.w.client = None; self.w.close(); self.w.deleteLater()
        self.app.processEvents(); self.temp.cleanup()

    def wait(self):
        deadline = time.monotonic()+15
        while self.w.busy and time.monotonic()<deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertFalse(self.w.busy)

    def assert_unchanged(self):
        self.assertEqual((self.w.capture, self.w.saved, self.w.replay, self.w.device_state), self.before)
        self.assertEqual(self.w.client.method_calls, [])

    def test_gui_worker_stats_no_serial_or_buffer_mutation(self):
        with patch('mvp5.ui.QFileDialog.getOpenFileNames', return_value=([str(f) for f in FILES], '')):
            self.w.analysis_button.click()
        self.wait()
        self.assertEqual(self.w.analysis_report['verified_records'], 6)
        self.assertEqual(self.w.analysis_table.rowCount(), 4)
        self.assertEqual(self.w.analysis_table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assert_unchanged()

    def test_report_gui_export_does_not_mark_raw_saved(self):
        self.w.show_analysis(analyze_records(FILES[:1]))
        for suffix in ('html','json'):
            target = Path(self.temp.name)/('report.'+suffix)
            with patch('mvp5.ui.QFileDialog.getSaveFileName', return_value=(str(target),'')):
                self.w.export_statistics(suffix)
            self.wait(); self.assertTrue(target.is_file())
        self.assert_unchanged()

    def test_cancel_dialog_retains_previous_report(self):
        report = analyze_records(FILES[:1]); self.w.show_analysis(report)
        with patch('mvp5.ui.QFileDialog.getOpenFileNames', return_value=([],'')):
            self.w.analyze_saved_records()
        self.assertIs(self.w.analysis_report, report); self.assert_unchanged()

    def test_offline_failure_preserves_device_known_state(self):
        with patch('mvp5.ui.QMessageBox.warning'):
            self.w.analysis_failed('bad offline input')
        self.assertEqual(self.w.device_state, 'FROZEN'); self.assert_unchanged()

    def test_analysis_busy_disables_export(self):
        self.w.show_analysis(analyze_records(FILES[:1])); self.w.busy=True; self.w.gate()
        self.assertFalse(self.w.analysis_button.isEnabled()); self.assertFalse(self.w.analysis_html.isEnabled())
        self.w.busy=False

    def test_default_statistics_page_has_room_for_four_rows(self):
        self.w.show(); self.w.show_analysis(analyze_records(FILES)); self.app.processEvents()
        self.assertGreaterEqual(self.w.analysis_table.viewport().height(),4*28)
        self.assertGreaterEqual(self.w.tabs.minimumHeight(),330)
        self.w.tabs.setCurrentIndex(1);self.app.processEvents()
        self.assertEqual(self.w.tabs.minimumHeight(),220)


if __name__ == '__main__': unittest.main()
