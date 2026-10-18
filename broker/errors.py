"""Erros de ferramenta (viram `{ok:false, error:{code,message}}`, nunca stack trace)."""
from __future__ import annotations


class ToolError(Exception):
    CODES = {"guardrail", "approval_denied", "approval_expired", "tool_error", "limit",
             "invalid_args", "unavailable", "forbidden_target"}

    def __init__(self, code: str, message: str):
        super().__init__(message)
        assert code in self.CODES, code
        self.code = code
        self.message = message
