import importlib.util
from pathlib import Path
import unittest

# The canonical preflight discovers tests with tests/ on sys.path, not repo root.
# Import by path so both broad discovery and explicit unittest invocation work.
spec = importlib.util.spec_from_file_location(
    "map_ci_scope", Path(__file__).resolve().parents[1] / "scripts" / "ci_scope.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
documentation_only = module.documentation_only


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
