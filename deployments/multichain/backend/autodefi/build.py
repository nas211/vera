"""Fingerprint runtime Python source without reading environment files or secrets."""
import hashlib
from pathlib import Path


def fingerprint(directory=None, *, runtime_root=None):
    if directory is not None:
        root=Path(directory)
        paths=sorted(root.rglob('*.py'))
    else:
        root=Path(runtime_root or Path(__file__).resolve().parents[2])
        paths=sorted({p for folder in ('backend','tools','components/why-discovery/backend/discovery')
                      for p in (root/folder).rglob('*.py')})
    result=hashlib.sha256()
    for path in paths:
        result.update(path.relative_to(root).as_posix().encode()+b'\0'+hashlib.sha256(path.read_bytes()).digest())
    return result.hexdigest()
