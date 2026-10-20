"""Run selected copied execution regressions with network/process/write guards.

Uses mock exchange adapters only. Writes are confined to a temporary directory.
This is not a live-exchange protection verification.
"""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "components" / "why-yellow"
sys.dont_write_bytecode = True
sys.path.insert(0, str(CORE))


def main():
    # Tests must not inherit production arming flags, credentials or database URLs.
    allowed = {"PATH", "LANG", "LC_ALL", "TZ"}
    for name in list(os.environ):
        if name not in allowed:
            del os.environ[name]
    os.chdir(CORE)
    temp = pathlib.Path(tempfile.mkdtemp(prefix="vera-offline-tests-")).resolve()
    tempfile.tempdir = str(temp)

    def writable(path):
        if isinstance(path, int) or path is None:
            return
        resolved = pathlib.Path(os.fsdecode(path)).resolve()
        if not resolved.is_relative_to(temp):
            raise RuntimeError("Vera check blocked a write outside its temporary directory")

    def guard(event, args):
        if event.startswith(("subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn")):
            raise RuntimeError("Vera check blocked process execution")
        if event.startswith("socket.") and event != "socket.__new__":
            raise RuntimeError("Vera check blocked network access")
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                writable(path)
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime"}:
            writable(args[0])
        if event in {"os.rename", "os.link", "os.symlink"}:
            writable(args[0])
            writable(args[1])
        if event == "sqlite3.connect" and args[0] != ":memory:":
            writable(args[0])

    sys.addaudithook(guard)
    names = sys.argv[1:] or [
        "tests.execution.test_global_dollar_stop",
        "tests.execution.test_loss_budget",
        "tests.execution.test_reconciliation",
        "tests.execution.test_targeted_reconciliation",
        "tests.execution.test_zz_real_orders",
    ]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"Vera offline checks: ran={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
