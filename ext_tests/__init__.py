"""Checks that span several official extensions, and helpers they share.

An extension's own tests are in its folder: ``extensions/<name>/tests/``.

They run against a checkout of SyntH (the Core), which provides the gateway, the packer and
the test helpers: set ``SYNTH_CORE`` to it and run pytest with the Core's environment::

    cd $SYNTH_CORE && SYNTH_CORE=$SYNTH_CORE DB_HOST=127.0.0.1 DB_PORT=1 \
        uv run pytest /path/to/this/repo
"""

from __future__ import annotations

import os
from pathlib import Path

#: This repository's root and the SyntH checkout the tests run against.
ROOT = Path(__file__).resolve().parent.parent
CORE = Path(os.environ.get("SYNTH_CORE") or ROOT.parent / "Synthetic_Heart").resolve()
