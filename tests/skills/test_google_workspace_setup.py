"""Google Workspace setup delegates dependency ownership to PM."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pm
import pytest


SETUP_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/setup.py"
)


@pytest.fixture()
def setup_module(monkeypatch):
    # setup.py exposes sibling imports for direct script execution.
    monkeypatch.syspath_prepend(str(SETUP_PATH.parent))
    spec = importlib.util.spec_from_file_location("google_workspace_setup", SETUP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("error", [None, pm.InstallError("venv", "sync refused")])
def test_explicit_install_uses_pm_and_reports_restart(setup_module, monkeypatch, capsys, error):
    sync = Mock(side_effect=error)
    monkeypatch.setattr(pm, "sync_venv", sync)
    # Even a successful old-interpreter probe must not bypass explicit sync.
    monkeypatch.setattr(pm, "ensure_import", Mock(side_effect=AssertionError("not a sync")))
    monkeypatch.setattr("subprocess.check_call", Mock(side_effect=AssertionError("ambient install")))

    assert setup_module.install_deps() is (error is None)
    sync.assert_called_once_with(["google"], explicit=True)
    output = capsys.readouterr().out
    if error is None:
        assert "restart" in output.lower()
    else:
        assert "sync refused" in output


def test_auth_uses_pm_import_check(setup_module, monkeypatch):
    ensure = Mock()
    monkeypatch.setattr(pm, "ensure_import", ensure)
    monkeypatch.setattr(pm, "sync_venv", Mock(side_effect=AssertionError("explicit sync during auth")))
    monkeypatch.setattr("subprocess.check_call", Mock(side_effect=AssertionError("ambient install")))

    setup_module._ensure_deps()

    ensure.assert_called_once_with("google")


def test_calendar_service_selects_only_calendar_oauth_scope(setup_module):
    assert setup_module.scopes_for_services("calendar") == [
        "https://www.googleapis.com/auth/calendar"
    ]


def test_all_services_remains_the_backward_compatible_default(setup_module):
    assert setup_module.scopes_for_services("all") == setup_module.SCOPES


def test_service_selection_rejects_unknown_google_service(setup_module):
    with pytest.raises(ValueError, match="Unknown Google service"):
        setup_module.scopes_for_services("calendar,mail")


def test_calendar_only_token_is_not_reported_as_incomplete_workspace_auth(setup_module):
    calendar_scope = "https://www.googleapis.com/auth/calendar"
    assert setup_module._missing_scopes_from_payload(
        {"scopes": [calendar_scope], "hermes_requested_scopes": [calendar_scope]}
    ) == []


def test_auth_url_persists_selected_scopes_for_callback(setup_module, monkeypatch, tmp_path):
    client_secret = tmp_path / "client.json"
    client_secret.write_text("{}", encoding="utf-8")
    pending = tmp_path / "pending.json"
    monkeypatch.setattr(setup_module, "CLIENT_SECRET_PATH", client_secret)
    monkeypatch.setattr(setup_module, "PENDING_AUTH_PATH", pending)
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    captured = {}

    class FakeFlow:
        code_verifier = "verifier"

        @classmethod
        def from_client_secrets_file(cls, _path, *, scopes, **_kwargs):
            captured["scopes"] = scopes
            return cls()

        def authorization_url(self, **_kwargs):
            return "https://accounts.google.test/authorize", "state"

    import sys
    from types import ModuleType

    oauthlib = ModuleType("google_auth_oauthlib")
    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.__dict__["Flow"] = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", oauthlib)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    setup_module.get_auth_url("calendar")

    assert captured["scopes"] == ["https://www.googleapis.com/auth/calendar"]
    assert setup_module.json.loads(pending.read_text(encoding="utf-8"))["scopes"] == captured["scopes"]


def test_auth_code_exchange_uses_scopes_saved_for_calendar(setup_module, monkeypatch, tmp_path):
    client_secret = tmp_path / "client.json"
    client_secret.write_text("{}", encoding="utf-8")
    pending = tmp_path / "pending.json"
    token = tmp_path / "token.json"
    calendar_scope = "https://www.googleapis.com/auth/calendar"
    pending.write_text(
        setup_module.json.dumps(
            {"state": "state", "code_verifier": "verifier", "scopes": [calendar_scope]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_module, "CLIENT_SECRET_PATH", client_secret)
    monkeypatch.setattr(setup_module, "PENDING_AUTH_PATH", pending)
    monkeypatch.setattr(setup_module, "TOKEN_PATH", token)
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    captured = {}

    class FakeCredentials:
        granted_scopes = [calendar_scope]

        def to_json(self):
            return setup_module.json.dumps({"token": "access", "scopes": [calendar_scope]})

    class FakeFlow:
        credentials = FakeCredentials()

        @classmethod
        def from_client_secrets_file(cls, _path, *, scopes, **_kwargs):
            captured["scopes"] = scopes
            return cls()

        def fetch_token(self, *, code):
            captured["code"] = code

    import sys
    from types import ModuleType

    oauthlib = ModuleType("google_auth_oauthlib")
    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.__dict__["Flow"] = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", oauthlib)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    setup_module.exchange_auth_code("auth-code")

    saved = setup_module.json.loads(token.read_text(encoding="utf-8"))
    assert captured == {"scopes": [calendar_scope], "code": "auth-code"}
    assert saved["hermes_requested_scopes"] == [calendar_scope]
    assert not pending.exists()


def test_cli_passes_selected_services_to_auth_url(setup_module, monkeypatch):
    captured = []
    monkeypatch.setattr("sys.argv", ["setup.py", "--auth-url", "--services", "calendar"])
    monkeypatch.setattr(setup_module, "get_auth_url", captured.append)

    setup_module.main()

    assert captured == ["calendar"]
