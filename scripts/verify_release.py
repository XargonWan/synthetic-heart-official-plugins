"""Check that a built catalog is signed with the key SyntH has pinned for this repository.

    python verify_release.py DIST public.key

Publishing a catalog signed with any other key would break every installation, so the
release is not made unless this passes. Needs the SyntH repository on PYTHONPATH.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from core.extensions.repo.index import key_fingerprint, parse_index, verify_index


def main(dist: Path, key_file: Path) -> int:
    public = key_file.read_text().strip()
    raw = (dist / "synth-repo.json").read_bytes()
    signature = (dist / "synth-repo.json.sig").read_bytes()
    problem = verify_index(raw, signature, public)
    if problem:
        print(f"the catalog does not verify with {key_file.name}: {problem}")
        return 1
    index = parse_index(raw)
    for package in index.packages:
        archive = dist / package.file
        data = archive.read_bytes()
        if len(data) != package.size or hashlib.sha256(data).hexdigest() != package.sha256:
            print(f"{package.file} does not match the catalog")
            return 1
    print(
        f"ok: {len(index.packages)} package(s), signed with {key_fingerprint(public)}"
    )
    for package in index.packages:
        print(f"  {package.id} {package.version} ({package.platform}, {package.size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
