"""Software-only fixtures and mocks; these tests are not new board captures."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mvp5.models import Config
from mvp5.paired_deadlines import paired_deadline_config, paired_capture_summary
from mvp5.workload_experiment import workload_config, run_workload, publish_summary
from mvp5.tests.test_workload_experiment import FixtureClient, records


def synthetic_pair():
    # Metadata-only unit input. It is never published as a measured capture.
    cfg = paired_deadline_config()
    rules = []
    for i, r in enumerate(cfg.rules):
        rules.append(dict(a_tick=1000, observed_b_tick=214000 if i<2 else 216000,
                          counts={'accepted':1}, result='TIMEOUT' if i%2==0 else 'PASS'))
    cap = SimpleNamespace(complete=True, metadata=dict(config=cfg.as_dict(), rules=rules,
                          enabled_mask=15, origin='hardware', crc_errors=0, transport_retries=0))
    audit = dict(status='verified', rules=[dict(rule_id=i, result=r['result'],
                 observed_delay_ms=4.26 if i<2 else 4.30) for i,r in enumerate(rules)])
    return cap, audit


class PairedDeadlineTests(unittest.TestCase):
    def test_wire_roundtrip_targets_limits_and_trigger_action(self):
        cfg=paired_deadline_config()
        self.assertEqual(len(cfg.to_bytes()),100)
        self.assertEqual(Config.from_bytes(cfg.to_bytes()),cfg)
        self.assertEqual(cfg.enabled_mask,15)
        self.assertEqual([r.max_ticks for r in cfg.rules],[175000,400000,175000,400000])
        self.assertEqual([r.min_ticks for r in cfg.rules],[0]*4)
        self.assertEqual([r.action for r in cfg.rules],[2,0,0,0])
        self.assertEqual([r.target_kind for r in cfg.rules],[0,0,1,1])
        self.assertEqual([r.target_channel for r in cfg.rules[:2]],[1,1])
        self.assertEqual([r.spi_value for r in cfg.rules[2:]],[0xc3,0xc3])

    def test_capture_covers_full_identity_and_both_missing_response_deadlines(self):
        cfg=paired_deadline_config()
        self.assertEqual(cfg.pre+1+cfg.post,65536)
        self.assertEqual(cfg.divisor,10)
        # UART ten-byte packet plus a 3.5 ms first timeout fits in prehistory.
        self.assertGreater(cfg.pre*cfg.divisor,175001+10*10*(50000000//115200))
        # Even a very early R0 result leaves time to prove 8 ms SPI timeout.
        self.assertGreater(cfg.post*cfg.divisor,400001+cfg.divisor)

    def test_explicit_none_selects_paired_preset_without_changing_scalar_modes(self):
        self.assertEqual(workload_config(None),paired_deadline_config())
        for value in (3.5,8):
            cfg=workload_config(value)
            self.assertEqual((cfg.enabled_mask,cfg.pre,cfg.post),(5,45000,20535))

    def test_same_A_same_B_and_actual_results_preserved_without_mutation(self):
        cap,audit=synthetic_pair();before=copy.deepcopy(cap.metadata)
        result=paired_capture_summary(cap,audit)
        self.assertTrue(result['same_transaction'])
        self.assertEqual(result['a_tick'],1000)
        self.assertEqual([p['results'] for p in result['pairs']],[['TIMEOUT','PASS']]*2)
        self.assertEqual(result['pairs'][0]['observed_delay_ms'],4.26)
        self.assertEqual(cap.metadata,before)

    def test_missing_response_is_none_not_zero(self):
        cap,audit=synthetic_pair()
        for r in cap.metadata['rules']:r.update(observed_b_tick=None,result='TIMEOUT')
        result=paired_capture_summary(cap,audit)
        self.assertTrue(all(p['observed_delay_ms'] is None for p in result['pairs']))
        self.assertEqual(result['pairs'][0]['results'],['TIMEOUT','TIMEOUT'])

    def test_different_start_rejected(self):
        cap,audit=synthetic_pair();cap.metadata['rules'][1]['a_tick']+=1
        with self.assertRaisesRegex(ValueError,'同一次A'):paired_capture_summary(cap,audit)

    def test_missing_start_rejected(self):
        cap,audit=synthetic_pair()
        for r in cap.metadata['rules']:r['a_tick']=None
        with self.assertRaisesRegex(ValueError,'同一次A'):paired_capture_summary(cap,audit)

    def test_different_observed_B_rejected_for_both_targets(self):
        for index in (1,3):
            cap,audit=synthetic_pair();cap.metadata['rules'][index]['observed_b_tick']+=1
            with self.assertRaisesRegex(ValueError,'观测的B不同'):paired_capture_summary(cap,audit)

    def test_nonunique_transaction_rejected(self):
        cap,audit=synthetic_pair();cap.metadata['rules'][2]['counts']['accepted']=2
        with self.assertRaisesRegex(ValueError,'各一次事务'):paired_capture_summary(cap,audit)

    def test_unaudited_or_missing_rule_rejected(self):
        cap,audit=synthetic_pair();audit['status']='ambiguous'
        with self.assertRaisesRegex(ValueError,'原始样点'):paired_capture_summary(cap,audit)
        audit['status']='verified';audit['rules'].pop()
        with self.assertRaisesRegex(ValueError,'原始样点'):paired_capture_summary(cap,audit)

    def test_custom_config_cannot_claim_standard_comparison(self):
        cap,audit=synthetic_pair();cap.metadata['config']['rules'][1]['max_ticks']-=1
        with self.assertRaisesRegex(ValueError,'预设'):paired_capture_summary(cap,audit)

    def test_expectation_does_not_overwrite_device_result(self):
        from mvp5.user_guidance import demo_expectation
        cap,_=synthetic_pair();before=copy.deepcopy(cap.metadata)
        identity=dict(verified=True,kind='workload',workload=dict(profile=2))
        self.assertTrue(demo_expectation(cap,identity)['matches'])
        cap.metadata['rules'][0]['result']='PASS'
        self.assertFalse(demo_expectation(cap,identity)['matches'])
        self.assertEqual(cap.metadata['rules'][0]['result'],'PASS')
        self.assertEqual(before['rules'][0]['result'],'TIMEOUT')
        cap.metadata['config']['pre']+=1
        self.assertIsNone(demo_expectation(cap,identity))

    def test_old_capture_cannot_pass_new_mode_or_be_released(self):
        # These old physical fixtures were captured with two rules, not four.
        with tempfile.TemporaryDirectory() as root:
            client=FixtureClient(records())
            with self.assertRaisesRegex(ValueError,'配置身份'):
                run_workload(client,Path(root)/'paired',None)
            self.assertEqual(client.actions,['configure','arm'])

    def test_mocked_pair_dispatch_stats_report_and_last_record_protection(self):
        # Mock only new pair analysis to exercise common workflow dispatch.
        # Historical captures stay unchanged; this is NOT physical pair proof.
        caps=records();old_cfg=workload_config(3.5)
        def fake_audit(cap):
            value=cap.metadata['rules'][0]['observed_delay_ticks']/50000 if 'observed_delay_ticks' in cap.metadata['rules'][0] else 4.2
            return dict(status='verified',rules=[dict(rule_id=i,result='PASS',observed_delay_ms=value) for i in range(4)])
        comparison=dict(same_transaction=True,pairs=[dict(target='GPIO',results=['TIMEOUT','PASS'],observed_delay_ms=4.2)])
        with tempfile.TemporaryDirectory() as root, \
             patch('mvp5.workload_experiment.workload_config',return_value=old_cfg), \
             patch('mvp5.workload_experiment.audit_capture',side_effect=fake_audit), \
             patch('mvp5.workload_experiment.paired_capture_summary',return_value=comparison) as checker:
            client=FixtureClient(caps);directory=Path(root)/'unit_only'
            result=run_workload(client,directory,None)
            self.assertTrue(result['report']['passed']);self.assertEqual(checker.call_count,6)
            self.assertEqual(client.actions.count('release'),5);self.assertEqual(client.state,'FROZEN')
            self.assertEqual(result['report']['experiment_kind'],'same_transaction_deadlines')
            self.assertFalse(result['report']['board_sound_light_supported'])
            self.assertIsNone(result['report']['deadline_ms'])
            self.assertTrue(all(set(g['rules'])=={'0','1','2','3'} for g in result['report']['groups']))
            report=json.loads((directory/'experiment.json').read_bytes())
            self.assertTrue(all(c['deadline_comparison']['same_transaction'] for c in report['cases']))
            html=(directory/'summary.html').read_text(encoding='utf-8')
            self.assertIn('同一次任务的两种期限',html);self.assertIn('8 ms规则',html)
            self.assertIn('SPI · 8 ms',html);self.assertNotIn('Nonems',html)
            self.assertIn('不对该配置启用判定灯或蜂鸣器',html)


class PairedDeadlineGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_third_preset_is_offline_draft_only_and_keeps_original_record(self):
        from mvp5.ui import MainWindow
        w=MainWindow();w.timer.stop()
        try:
            cap=records()[0];w.capture=cap;w.saved=w.replay=True
            before=copy.deepcopy(cap)
            w.workload_deadline.setCurrentIndex(2);w.workload_preset.click()
            self.assertIn('仅波形',w.workload_deadline.currentText())
            self.assertIsNone(w.selected_workload_deadline())
            self.assertEqual(w.read_draft().to_bytes(),paired_deadline_config().to_bytes())
            self.assertEqual(w.capture,before);self.assertIsNone(w.client)
            self.assertFalse(w.workload_run.isEnabled())
        finally:
            w.capture=None;w.close();w.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
