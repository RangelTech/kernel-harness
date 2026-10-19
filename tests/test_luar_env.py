from broker.env import available_secrets
from harness.security import Redactor


class _Ctx:
    def __init__(self, creds):
        self.credentials = creds

    def granted(self, kind):
        return False


def test_luar_vars_injected_and_redacted():
    creds = {"luar": {"url": "https://ia.exemplo.net", "token": "abc.def-0123456789", "account_id": 7}}
    have = available_secrets(_Ctx(creds))
    assert have["LUAR_URL"] == "https://ia.exemplo.net"
    assert have["LUAR_CTX"] == "abc.def-0123456789"
    assert have["LUAR_ACCOUNT_ID"] == "7"
    assert "[REDACTED]" in Redactor.from_credentials(creds).scrub("x abc.def-0123456789 y")


def test_no_luar_without_credentials():
    assert "LUAR_CTX" not in available_secrets(_Ctx({}))
