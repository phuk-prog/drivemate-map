import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DmnRightsTest(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('dmn_rights', ROOT / 'scripts/dmn_rights.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, directory):
        p = Path(directory)
        (p / 'dmn-uk.json').write_text(json.dumps({'release': 'dmn-uk-20261028-15', 'source': 'actual recorded source label'}))
        return p

    def test_existing_bundle_bound_to_index_and_verified(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d)
            m.prepare(p, ROOT / 'licenses')
            doc = json.loads((p / 'dmn-uk.json').read_text())
            self.assertEqual(set(doc['rights']), set(m.FILES))
            assets = [{'name': n, 'size': v['bytes'], 'digest': 'sha256:' + v['sha256']} for n, v in doc['rights'].items()]
            m.verify(doc, assets)
            for name, entry in doc['rights'].items():
                self.assertEqual(entry['tag'], doc['release'])
                self.assertEqual((p / name).read_bytes(), (ROOT / 'licenses' / name).read_bytes())
            for field, bad in [('size', 0), ('digest', 'sha256:' + '0' * 64)]:
                broken = [dict(a) for a in assets]
                broken[0][field] = bad
                with self.assertRaises(ValueError): m.verify(doc, broken)
            with self.assertRaises(ValueError): m.verify(doc, assets[1:])
            with self.assertRaises(ValueError): m.verify({'release': doc['release']}, assets)

    def test_missing_notice_refused_before_index_change(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d)
            before = (p / 'dmn-uk.json').read_bytes()
            with self.assertRaises(ValueError): m.prepare(p, p / 'missing')
            self.assertEqual(before, (p / 'dmn-uk.json').read_bytes())

    def test_workflow_checks_rights_before_pointer_upload(self):
        text = (ROOT / '.github/workflows/dmn-pack.yml').read_text()
        self.assertIn('python3 scripts/dmn_rights.py prepare parts', text)
        self.assertIn('parts/NOTICE.md', text)
        self.assertLess(text.index('python3 scripts/dmn_rights.py verify parts/dmn-uk.json assets.json'), text.index('gh release upload dmn-uk -R "$GITHUB_REPOSITORY" --clobber parts/dmn-uk.json'))

if __name__ == '__main__': unittest.main()
