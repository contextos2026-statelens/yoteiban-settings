import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import re, unittest
from pathlib import Path

PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)(?:\+81|0)\d{1,4}-?\d{2,4}-?\d{3,4}(?!\d)"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile("automations" + "/webhook/"),
]
class FixedValueTests(unittest.TestCase):
    def test_no_fixed_values(self):
        root = Path(R) / 'core'
        findings=[]
        for path in root.rglob('*'):
            if not path.is_file(): continue
            try: content=path.read_text(encoding='utf-8')
            except UnicodeDecodeError: continue
            content=re.sub(r"example\.(?:com|test)", "", content, flags=re.I)
            findings += [(str(path), p.pattern) for p in PATTERNS if p.search(content)]
        self.assertEqual(findings, [])
if __name__ == '__main__': unittest.main()
