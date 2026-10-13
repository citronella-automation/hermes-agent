import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT = Path(__file__).resolve().parents[2] / "skills/productivity/google-workspace/scripts/calendar_events.py"
spec = importlib.util.spec_from_file_location("calendar_events", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_create_sends_supported_fields_to_primary_and_reads_event_back(monkeypatch):
    service = MagicMock()
    events = service.events.return_value
    monkeypatch.setattr(module, "build_service", lambda: service)
    event = {
        "id": "new-id",
        "summary": "Coffee",
        "start": {"dateTime": "2026-10-14T09:00:00+08:00"},
        "end": {"dateTime": "2026-10-14T10:00:00+08:00"},
        "colorId": "3",
    }
    events.insert.return_value.execute.return_value = event
    events.get.return_value.execute.return_value = event
    args = module.parse_args(["create", "--summary", "Coffee", "--start", "2026-10-14T09:00:00+08:00", "--end", "2026-10-14T10:00:00+08:00", "--color-id", "3"])
    module.run(args)
    call = events.insert.call_args.kwargs
    assert call["calendarId"] == "primary"
    assert call["body"] == {
        "summary": "Coffee",
        "start": {"dateTime": "2026-10-14T09:00:00+08:00"},
        "end": {"dateTime": "2026-10-14T10:00:00+08:00"},
        "colorId": "3",
    }
    assert "eventLabelId" not in call["body"]
    events.get.assert_called_once_with(calendarId="primary", eventId="new-id")


def test_update_patches_only_explicit_fields_and_reads_event_back(monkeypatch):
    service = MagicMock()
    events = service.events.return_value
    monkeypatch.setattr(module, "build_service", lambda: service)
    events.patch.return_value.execute.return_value = {"id": "evt", "summary": "Renamed"}
    events.get.return_value.execute.return_value = {"id": "evt", "summary": "Renamed"}
    args = module.parse_args(["update", "evt", "--summary", "Renamed"])
    module.run(args)
    call = events.patch.call_args.kwargs
    assert call["calendarId"] == "primary"
    assert call["eventId"] == "evt"
    assert call["body"] == {"summary": "Renamed"}
    events.get.assert_called_once_with(calendarId="primary", eventId="evt")


def test_social_category_falls_back_to_legacy_purple_on_create(monkeypatch):
    service = MagicMock()
    events = service.events.return_value
    events.insert.return_value.execute.return_value = {"id": "new"}
    events.get.return_value.execute.return_value = {"id": "new", "summary": "Coffee"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    args = module.parse_args(["create", "--summary", "Coffee", "--start", "2026-10-14T09:00:00+08:00", "--end", "2026-10-14T10:00:00+08:00", "--category", "social"])
    module.run(args)
    assert events.insert.call_args.kwargs["body"]["colorId"] == "3"


import pytest


@pytest.mark.parametrize("category,color_id", [("cs3210", "9"), ("cs1231s", "10"), ("admin", "8"), ("research", "2"), ("lab", "10"), ("social", "3"), ("health", "11")])
def test_missing_category_label_update_uses_legacy_fallback(monkeypatch, category, color_id):
    service = MagicMock()
    events = service.events.return_value
    events.get.return_value.execute.return_value = {"id": "evt"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    service.calendars.return_value.get.return_value.execute.return_value = {}
    events.patch.return_value.uri = "https://example.test/events/evt?alt=json"
    module.run(module.parse_args(["update", "evt", "--category", category]))
    assert "eventLabelVersion" not in events.patch.return_value.uri
    events.patch.assert_called_once_with(calendarId="primary", eventId="evt", body={"colorId": color_id})
    events.get.assert_called_once_with(calendarId="primary", eventId="evt")


@pytest.mark.parametrize("action", ["create", "update", "edit"])
def test_category_and_explicit_color_are_mutually_exclusive(action, capsys):
    argv = [action, "evt"] if action != "create" else [action, "--summary", "Coffee", "--start", "2026-10-14T09:00:00Z", "--end", "2026-10-14T10:00:00Z"]
    with pytest.raises(SystemExit):
        module.parse_args(argv + ["--category", "social", "--color-id", "11"])
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("category", ["unknown", "Cocoa"])
def test_unknown_or_unwritable_category_is_rejected(category, capsys):
    with pytest.raises(SystemExit):
        module.parse_args(["update", "evt", "--category", category])
    assert "invalid choice" in capsys.readouterr().err


def test_modern_social_label_is_resolved_and_verified(monkeypatch, capsys):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {
        "labelProperties": {"eventLabels": [{"name": "Grape", "id": "grape-id"}]}}
    events = service.events.return_value
    events.patch.return_value.uri = "https://example.test/events/evt?alt=json"
    events.get.return_value.uri = "https://example.test/events/evt?alt=json"
    events.get.return_value.execute.return_value = {"id": "evt", "eventLabelId": "grape-id"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    module.run(module.parse_args(["update", "evt", "--category", "social"]))
    assert events.patch.call_args.kwargs["body"] == {"eventLabelId": "grape-id"}
    assert "eventLabelVersion=1" in events.patch.return_value.uri
    assert "eventLabelVersion=1" in events.get.return_value.uri
    assert '"eventLabelId": "grape-id"' in capsys.readouterr().out


@pytest.mark.parametrize("category,name", list(module.CATEGORY_LABEL_NAMES.items()))
def test_exact_category_resolution(category, name):
    service = MagicMock()
    labels = [] if name is None else [{"id": "exact", "name": name}]
    service.calendars.return_value.get.return_value.execute.return_value = {"labelProperties": {"eventLabels": labels}}
    label_id, fallback = module.resolve_label(service, module.parse_args(["update", "evt", "--category", category]))
    assert label_id == (None if name is None else "exact")
    assert bool(fallback) == (name is None)


@pytest.mark.parametrize("labels", [[], [{"id": "unnamed", "backgroundColor": "#795548"}]])
def test_cocoa_missing_name_has_no_fallback(labels, monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {"labelProperties": {"eventLabels": labels}}
    monkeypatch.setattr(module, "build_service", lambda: service)
    with pytest.raises(SystemExit, match="no legacy equivalent"):
        module.run(module.parse_args(["update", "evt", "--category", "extracurricular"]))
    service.events.assert_not_called()


def test_ambiguous_name_is_not_silently_substituted(monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {"labelProperties": {"eventLabels": [{"name": "Grape", "id": "a"}, {"name": "Grape", "id": "b"}]}}
    monkeypatch.setattr(module, "build_service", lambda: service)
    with pytest.raises(SystemExit, match="Ambiguous"):
        module.run(module.parse_args(["update", "evt", "--category", "social"]))
    service.events.assert_not_called()


def test_metadata_network_error_is_not_fallback(monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.side_effect = RuntimeError("network/auth")
    monkeypatch.setattr(module, "build_service", lambda: service)
    with pytest.raises(RuntimeError, match="network/auth"):
        module.run(module.parse_args(["update", "evt", "--category", "social"]))
    service.events.assert_not_called()


@pytest.mark.parametrize("option,value", [("--category", "social"), ("--label-name", "Grape"), ("--event-label-id", "a")])
def test_legacy_override_excludes_all_modern_options(option, value):
    with pytest.raises(SystemExit):
        module.parse_args(["update", "evt", "--color-id", "3", option, value])


def test_unnamed_verified_id_supports_cocoa(monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {"labelProperties": {"eventLabels": [{"id": "cocoa", "backgroundColor": "#795548"}]}}
    args = module.parse_args(["update", "evt", "--event-label-id", "cocoa"])
    assert module.resolve_label(service, args) == ("cocoa", None)


def test_fallback_reports_and_omits_modern_query(monkeypatch, capsys):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {}
    service.events.return_value.patch.return_value.uri = "https://example.test"
    service.events.return_value.get.return_value.execute.return_value = {"id": "evt", "colorId": "3"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    module.run(module.parse_args(["update", "evt", "--category", "social"]))
    assert service.events.return_value.patch.return_value.uri == "https://example.test"
    assert "colorFallback" in capsys.readouterr().out


@pytest.mark.parametrize("metadata", [{"labelProperties": None}, {"labelProperties": {"eventLabels": None}}])
def test_null_metadata_uses_reported_fallback(metadata):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = metadata
    args = module.parse_args(["update", "evt", "--category", "social"])
    label_id, fallback = module.resolve_label(service, args)
    assert label_id is None
    assert fallback and args.color_id == "3"


def test_modern_create_serializes_label_and_verifies_readback(monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {
        "labelProperties": {"eventLabels": [{"name": "Cocoa", "id": "cocoa"}]}}
    events = service.events.return_value
    events.insert.return_value.uri = "https://example.test/events"
    events.insert.return_value.execute.return_value = {"id": "new"}
    events.get.return_value.uri = "https://example.test/events/new"
    events.get.return_value.execute.return_value = {"id": "new", "eventLabelId": "cocoa"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    module.run(module.parse_args(["create", "--summary", "Club", "--start", "2026-10-14T09:00:00Z", "--end", "2026-10-14T10:00:00Z", "--category", "extracurricular"]))
    body = events.insert.call_args.kwargs["body"]
    assert body["eventLabelId"] == "cocoa" and "colorId" not in body
    assert events.insert.return_value.uri.endswith("?eventLabelVersion=1")
    assert events.get.return_value.uri.endswith("?eventLabelVersion=1")


def test_modern_readback_mismatch_is_an_error(monkeypatch):
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {
        "labelProperties": {"eventLabels": [{"name": "Grape", "id": "grape"}]}}
    events = service.events.return_value
    events.patch.return_value.uri = "https://example.test"
    events.get.return_value.uri = "https://example.test"
    events.get.return_value.execute.return_value = {"id": "evt", "eventLabelId": "other"}
    monkeypatch.setattr(module, "build_service", lambda: service)
    with pytest.raises(SystemExit, match="readback mismatch"):
        module.run(module.parse_args(["update", "evt", "--label-name", "Grape"]))


def test_datetime_requires_an_explicit_timezone():
    import pytest

    with pytest.raises(SystemExit):
        module.parse_args(["create", "--summary", "Coffee", "--start", "2026-10-14T09:00:00", "--end", "2026-10-14T10:00:00"])
