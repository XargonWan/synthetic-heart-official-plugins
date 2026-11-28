"""Tests of your extensions.

They run against a checkout of SyntH (the Core), which provides the gateway, the packer and
the test helpers: set ``SYNTH_CORE`` to it (default: ``../Synthetic_Heart``, next to this
repository) and run pytest with the Core's environment::

    scripts/test.sh                      # all tests
    scripts/test.sh -k hello -x          # extra arguments go to pytest
"""

from __future__ import annotations

import os
from pathlib import Path

#: This repository's root and the SyntH checkout the tests run against.
ROOT = Path(__file__).resolve().parent.parent
CORE = Path(os.environ.get("SYNTH_CORE") or ROOT.parent / "Synthetic_Heart").resolve()
