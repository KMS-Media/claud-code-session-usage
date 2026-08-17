"""Load the hyphenated `usage-status.py` script as an importable module.

The script is a standalone tool meant to be copied to ~/.claude/scripts/, so it
deliberately isn't packaged. `usage-status` isn't a valid Python identifier, so
tests can't just `import usage-status` — load it by file path instead and expose
it under the name `usage_status`.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent / "usage-status.py"


@pytest.fixture
def usage_status():
    spec = importlib.util.spec_from_file_location("usage_status", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["usage_status"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["usage_status"]
