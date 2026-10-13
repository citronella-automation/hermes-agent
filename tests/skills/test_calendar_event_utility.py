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


def test_datetime_requires_an_explicit_timezone():
    import pytest

    with pytest.raises(SystemExit):
        module.parse_args(["create", "--summary", "Coffee", "--start", "2026-10-14T09:00:00", "--end", "2026-10-14T10:00:00"])
