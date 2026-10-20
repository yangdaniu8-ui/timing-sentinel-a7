"""Integrity checks only: scratch fixtures, no serial/programmer/device imports."""
import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
SCRIPT=Path(__file__).with_name('verify_package.py')

class VerifyTests(unittest.TestCase):
    def run_case(self, *, name='input.txt', data=b'original', expected=b'original', size=None, omit=False, optimize=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            if not omit:(root/'input.txt').write_bytes(data)
            m=dict(schema=1,files={name:dict(bytes=len(expected) if size is None else size,sha256=hashlib.sha256(expected).hexdigest())})
            (root/'source_manifest.json').write_text(json.dumps(m))
            return subprocess.run([sys.executable,*(['-O'] if optimize else []),str(SCRIPT),'--root',str(root)],capture_output=True).returncode
    def test_intact(self):self.assertEqual(self.run_case(),0)
    def test_changed_same_length(self):self.assertNotEqual(self.run_case(data=b'changed!'),0)
    def test_wrong_size(self):self.assertNotEqual(self.run_case(size=123),0)
    def test_missing(self):self.assertNotEqual(self.run_case(omit=True),0)
    def test_parent_traversal(self):self.assertNotEqual(self.run_case(name='../input.txt'),0)
    def test_absolute_drive(self):self.assertNotEqual(self.run_case(name='C:/input.txt'),0)
    def test_optimized_python_cannot_skip_hash(self):self.assertNotEqual(self.run_case(data=b'changed!',optimize=True),0)

if __name__=='__main__':unittest.main(verbosity=2)
