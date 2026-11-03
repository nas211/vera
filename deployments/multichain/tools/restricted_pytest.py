"""Run unchanged pytest assertions with periodic selector wakeups in restricted executors.

This is an in-process test transport accommodation, not a networking test.
Production asyncio behavior is never patched.
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

original_run = asyncio.run


def run(coro, **kwargs):
    async def wrapped():
        loop = asyncio.get_running_loop()
        handle = None
        def pulse():
            nonlocal handle
            handle = loop.call_later(.01, pulse)
        pulse()
        try:
            return await coro
        finally:
            try:
                await loop.shutdown_default_executor()
            finally:
                handle.cancel()
    return original_run(wrapped(), **kwargs)


if __name__ == '__main__':
    asyncio.run = run
    raise SystemExit(pytest.main(sys.argv[1:]))
