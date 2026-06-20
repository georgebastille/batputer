"""Regression tests for get_google_service scope-upgrade detection.

The bug: passing `scopes` to Credentials.from_authorized_user_file sets
creds.scopes to the *requested* scopes, so a has_scopes() check always passed
and the calendar scope was never re-consented. We now read the *granted* scopes
from the token file itself.
"""
import json

import google.oauth2.credentials as gcreds
import google_auth_oauthlib.flow as gflow
import googleapiclient.discovery as gdiscovery

from connectors.google_auth import CALENDAR_EVENTS, GMAIL_READONLY, get_google_service


class _FakeCreds:
    def __init__(self, scopes, valid=True, expired=False, refresh_token=None):
        self._scopes = scopes
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token

    def to_json(self):
        return json.dumps({"token": "x", "scopes": self._scopes})


def _patch(monkeypatch, *, loaded_creds, flow_scopes):
    monkeypatch.setattr(gcreds.Credentials, "from_authorized_user_file",
                        staticmethod(lambda path, scopes: loaded_creds))
    flow = type("F", (), {"run_local_server": lambda self, port=0: _FakeCreds(flow_scopes)})()
    monkeypatch.setattr(gflow.InstalledAppFlow, "from_client_secrets_file",
                        staticmethod(lambda *a, **k: _patch.flow_called.append(True) or flow))
    monkeypatch.setattr(gdiscovery, "build", lambda *a, **k: "SERVICE")
    _patch.flow_called = []


def test_missing_scope_triggers_reconsent(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"token": "x", "scopes": [GMAIL_READONLY]}))  # gmail only
    _patch(monkeypatch, loaded_creds=_FakeCreds([GMAIL_READONLY], valid=True),
           flow_scopes=[GMAIL_READONLY, CALENDAR_EVENTS])

    svc = get_google_service("calendar", "v3", str(token), [GMAIL_READONLY, CALENDAR_EVENTS])

    assert svc == "SERVICE"
    assert _patch.flow_called  # re-consent ran
    assert CALENDAR_EVENTS in json.loads(token.read_text())["scopes"]  # token upgraded


def test_sufficient_scopes_skip_reconsent(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"token": "x", "scopes": [GMAIL_READONLY, CALENDAR_EVENTS]}))
    _patch(monkeypatch, loaded_creds=_FakeCreds([GMAIL_READONLY, CALENDAR_EVENTS], valid=True),
           flow_scopes=[GMAIL_READONLY, CALENDAR_EVENTS])

    svc = get_google_service("calendar", "v3", str(token), [GMAIL_READONLY, CALENDAR_EVENTS])

    assert svc == "SERVICE"
    assert not _patch.flow_called  # no browser prompt needed
