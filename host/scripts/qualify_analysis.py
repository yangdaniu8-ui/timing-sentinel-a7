"""Offline source qualification; no serial handles and no new board transactions."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time

HOST=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOST))
from mvp5 import __version__
from mvp5.analysis import analyze_records,export_analysis
from mvp5.store import blob,digest,load_record,save_record
from mvp5.ui import MainWindow
from PySide6.QtWidgets import QApplication

RECORDS=Path('D:/fpga_projects/timing_sentinel_a7_compare/data/demo_recovery_20261011_b03/restored_hardware_six')
FORMAL=Path('D:/fpga_projects/timing_sentinel_a7_compare/data/formal_20261011_b02/baseline')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=dict(passed=False,app_version=__version__,serial_opened=False,new_hardware_transactions=0,
                independently_calibrated=False,tests=[],input_sha256={})
    try:
        for name,start in [('mvp5','mvp5/tests'),('mvp4','mvp4/tests'),('legacy','tests')]:
            tested=subprocess.run([sys.executable,'-m','unittest','discover','-s',start,'-v'],cwd=HOST,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            log=tested.stdout+'\n'+tested.stderr;(out/(name+'_tests.log')).write_text(log,encoding='utf-8')
            result['tests'].append(dict(suite=name,returncode=tested.returncode,log_sha256=digest(log.encode('utf-8'))))
            assert tested.returncode==0 and 'Traceback' not in log,(name,log[-5000:])
        files=sorted(RECORDS.glob('*.tscap'));assert len(files)==6
        originals={p:digest(p.read_bytes()) for p in files+[RECORDS/'experiment.json',FORMAL/'experiment.json']}
        six=analyze_records(files+[files[0]])
        assert six['verified_records']==6 and six['unique_capture_contents']==6
        assert six['source_status_counts']['duplicate']==1
        export_analysis(six,out/'six_scene_analysis.json');export_analysis(six,out/'six_scene_analysis.html')
        cap=load_record(files[0]);partial=replace(cap,samples=cap.samples[:41]);save_record(partial,out/'partial.tscap')
        assert analyze_records([out/'partial.tscap'])['source_status_counts']=={'excluded':1}
        original_formal=json.loads((FORMAL/'experiment.json').read_bytes())
        formal_files=[FORMAL/case['file'] for case in original_formal['cases']]
        assert len(formal_files)==1000
        for path,case in zip(formal_files,original_formal['cases']):
            original_sha=digest(path.read_bytes());assert original_sha==case['sha256']
            originals[path]=original_sha
        started=time.monotonic()
        formal=analyze_records(formal_files)
        assert formal['verified_records']==1000,formal['source_status_counts']
        assert formal['original_failed_experiment_contexts']==1
        assert len(formal['saved_transport_warning_snapshots'])==725
        export_analysis(formal,out/'baseline_analysis.json');export_analysis(formal,out/'baseline_analysis.html')
        result['baseline']=dict(selected_files=1000,verified_records=formal['verified_records'],
            original_failed_experiment_contexts=1,warning_snapshots=725,seconds=round(time.monotonic()-started,3),
            claim='offline_raw_audit_not_formal_comparison_pass')
        app=QApplication.instance() or QApplication([]);w=MainWindow();w.timer.stop();w.show()
        w.replay=w.saved=True;w.show_capture(cap);w.show_analysis(six);app.processEvents()
        assert w.analysis_table.rowCount()==4 and not w.client
        assert w.grab().save(str(out/'analysis_gui.png'))
        w.tabs.setCurrentIndex(1);app.processEvents();assert w.grab().save(str(out/'waveform_gui.png'))
        w.capture=None;w.close();app.processEvents()
        assert all(digest(p.read_bytes())==sha for p,sha in originals.items())
        result.update(passed=True,input_sha256={str(p):sha for p,sha in originals.items()},
                      selected_six=six['source_status_counts'],original_evidence_unchanged=True)
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        (out/'qualification.json').write_bytes(blob(result))
    print('ANALYSIS_SOURCE_QUALIFICATION_PASS '+str(out),flush=True)


if __name__=='__main__':main()
