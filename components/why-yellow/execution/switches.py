"""Two independent switches (audit F-05):
  allow_new_entries          - gates ONLY new-risk submissions
  manage_existing_positions  - observe / reconcile / protect / close what exists
Turning entries off must never stop management. The ledger is the source of
truth; a JSON mirror lets yellow_exec.py (CLI) honour the same switch."""
from __future__ import annotations
import json, os, tempfile
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Switches:
    allow_new_entries: bool = False
    manage_existing_positions: bool = True
    updated_ts: float = 0.0
    reason: str = ""


def write_mirror(path: str, sw: Switches) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sw-")
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(sw), f)
    os.replace(tmp, path)


def read_mirror(path: str) -> Switches:
    try:
        with open(path) as f:
            j = json.load(f)
        return Switches(bool(j.get("allow_new_entries", False)), bool(j.get("manage_existing_positions", True)),
                        float(j.get("updated_ts", 0)), str(j.get("reason", "")))
    except (OSError, ValueError):
        # fail closed for entries, open for management
        return Switches(False, True, 0.0, "mirror_unreadable")
