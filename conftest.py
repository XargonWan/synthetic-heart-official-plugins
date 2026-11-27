"""Puts the SyntH checkout on the import path, so the tests can use the gateway and its helpers."""

from __future__ import annotations

import os
import sys

from ext_tests import CORE

os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault(
    "DB_PORT", "1"
)  # nothing listens here: tests never touch a real database
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
