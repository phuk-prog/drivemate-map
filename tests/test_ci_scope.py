import unittest
from scripts.ci_scope import documentation_only


class CiScopeTests(unittest.TestCase):
    def test_docs_only(self):
        self.assertTrue(documentation_only(["README.md", ".github/copilot-instructions.md", "CLAUDE.md"]))
    def test_non_docs_require_validation(self):
        self.assertFalse(documentation_only([]))
        for name in ["scripts/uk_map.py", "data/source.csv", "tests/fixture.json", ".github/workflows/source-checks.yml", "docs/road.txt"]:
            self.assertFalse(documentation_only([name]))
        self.assertFalse(documentation_only(["README.md", "scripts/ci_scope.py"]))


if __name__ == "__main__":
    unittest.main()
