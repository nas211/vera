"""Scalp-only software protection explicitly approved by the account owner.

The owner approved the $1 bot-managed emergency close on 2026-10-11 while
keeping existing sizing and take-profit execution. Swing's loss policy remains
in execution.sizing and is not changed by this exception.
"""
from execution.sizing import entry_loss_limit

SOFTWARE_EMERGENCY_ACCOUNTS = frozenset({
    'yellow:8f3efa87-f7c0-4c21-ba1e-46b207613f96',
})


def software_emergency_authorized(namespace):
    return namespace in SOFTWARE_EMERGENCY_ACCOUNTS


def protected_execution_required(namespace):
    return entry_loss_limit(namespace) is not None and not software_emergency_authorized(namespace)
