"""agent-handoff-check: check that every agent-to-agent handoff only narrows authority."""

from .audit import AuditLog, verify
from .checker import ACT, BLOCK, ESCALATE, check

__all__ = ["check", "ACT", "ESCALATE", "BLOCK", "AuditLog", "verify"]
__version__ = "0.2.0"
