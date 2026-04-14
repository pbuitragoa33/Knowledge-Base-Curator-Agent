import sys
from pathlib import Path
import unittest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

ISSUE_TEST_MODULES = [
    "test_issue_11",
    "test_issue_12",
    "test_issue_13",
    "test_issue_14",
    "test_issue_15",
    "test_issue_19",
]


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()

    for module_name in ISSUE_TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(module_name))

    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
