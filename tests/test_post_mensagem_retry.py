"""Gate FINAL T: o kernel insiste em gravar a resposta quando o backend reinicia no fim da run."""
from __future__ import annotations

import httpx
import pytest

from harness.backend import BackendError, call_with_retry


async def test_retries_while_backend_is_down_then_succeeds():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return "ok"

    assert await call_with_retry(call, delays=(0.0, 0.0, 0.0)) == "ok"
    assert calls["n"] == 3


async def test_5xx_is_retried_and_final_error_is_raised():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        raise BackendError(503, "restarting")

    with pytest.raises(BackendError):
        await call_with_retry(call, delays=(0.0, 0.0))
    assert calls["n"] == 3


async def test_4xx_is_definitive_no_retry():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        raise BackendError(401, "Execucao encerrada")

    with pytest.raises(BackendError):
        await call_with_retry(call, delays=(0.0, 0.0))
    assert calls["n"] == 1
