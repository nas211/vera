"""Fingerprint the running backend's source without reading configuration or secrets."""
import hashlib
from pathlib import Path


def fingerprint(directory=None):
    directory = Path(directory or Path(__file__).parent)
    result = hashlib.sha256()
    for path in sorted(directory.glob('*.py')):
        result.update(path.name.encode()+b'\0'+hashlib.sha256(path.read_bytes()).digest())
    return result.hexdigest()
