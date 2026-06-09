"""Test setup: point the toolkit at a placeholder config and a temp output dir.

These env vars must be set before `src` is imported, so they live at module top
(conftest is imported before the test modules).
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    'FETCH_CONFIG', str(Path(__file__).parent / 'fixtures' / 'config.yml')
)
os.environ.setdefault('FETCH_OUTPUT_DIR', tempfile.mkdtemp(prefix='fetch-test-'))
