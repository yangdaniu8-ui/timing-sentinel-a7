"""Negative tests of the read-only evidence verifier; scratch data only."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import verify_evidence as v


class EvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()

    def test_unsafe_parent_path(self):
        with self.assertRaises(ValueError):v.safe(self.root,'../outside.txt')

    def test_unsafe_drive_path(self):
        with self.assertRaises(ValueError):v.safe(self.root,'C:/outside.txt')

    def test_missing_report(self):
        obj=dict(schema=1,files={'a.rpt':dict(bytes=1,sha256='0'*64)})
        (self.root/'manifest.json').write_text(json.dumps(obj))
        with self.assertRaises(ValueError):v.verify(self.root)

    def test_changed_report(self):
        (self.root/'a.rpt').write_bytes(b'wrong')
        obj=dict(schema=1,files={'a.rpt':dict(bytes=5,sha256=hashlib.sha256(b'right').hexdigest())})
        (self.root/'manifest.json').write_text(json.dumps(obj))
        with self.assertRaises(ValueError):v.verify(self.root)

    def test_extra_record_members(self):
        p=self.root/'test.tscap'
        with zipfile.ZipFile(p,'w') as archive:archive.writestr('unexpected.txt','x')
        with self.assertRaises(ValueError):v.metadata(p)

    def test_changed_record_payload(self):
        p=self.root/'test.tscap'
        entries={'metadata.json':b'{}','samples.bin':b'x','events.bin':b''}
        manifest=dict(schema_version=2,profile='mvp5',files={n:dict(size=len(b),sha256=hashlib.sha256(b).hexdigest()) for n,b in entries.items()})
        with zipfile.ZipFile(p,'w') as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            for n,b in entries.items():archive.writestr(n,b if n!='samples.bin' else b'y')
        with self.assertRaises(ValueError):v.metadata(p)


if __name__=='__main__':unittest.main(verbosity=2)
