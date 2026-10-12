"""Tests for the date-scoped primary Google Calendar reader."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/calendar_day.py"
)


@pytest.fixture()
def calendar_day_module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT_PATH.parent))
    spec = importlib.util.spec_from_file_location("google_calendar_day", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_day_window_defaults_to_singapore_time(calendar_day_module):
    start, end = calendar_day_module.day_window(date(2026, 10, 12))
    assert start.isoformat() == "2026-10-12T00:00:00+08:00"
    assert end.isoformat() == "2026-10-13T00:00:00+08:00"


def test_day_window_accepts_an_explicit_timezone(calendar_day_module):
    start, end = calendar_day_module.day_window(date(2026, 10, 12), "UTC")
    assert start.isoformat() == "2026-10-12T00:00:00+00:00"
    assert end.isoformat() == "2026-10-13T00:00:00+00:00"


def test_modern_event_label_resolves_name_and_color(calendar_day_module):
    event = {"eventLabelId": "label-cobalt", "colorId": "3"}
    labels = [{"id": "label-cobalt", "backgroundColor": "#4285f4"}]
    color = calendar_day_module.resolve_event_color(event, labels, {}, {})
    assert color == {
        "name": "Cobalt",
        "background": "#4285f4",
        "foreground": None,
        "source": "event_label",
    }


@pytest.mark.parametrize(
    ("background", "name"),
    [
        ("#795548", "Cocoa"),
        ("#e67c73", "Flamingo"),
        ("#d50000", "Tomato"),
        ("#f4511e", "Tangerine"),
        ("#ef6c00", "Pumpkin"),
        ("#f09300", "Mango"),
        ("#009688", "Eucalyptus"),
        ("#0b8043", "Basil"),
        ("#7cb342", "Pistachio"),
        ("#c0ca33", "Avocado"),
        ("#e4c441", "Citron"),
        ("#f6bf26", "Banana"),
        ("#33b679", "Sage"),
        ("#039be5", "Peacock"),
        ("#4285f4", "Cobalt"),
        ("#3f51b5", "Blueberry"),
        ("#7986cb", "Lavender"),
        ("#b39ddb", "Wisteria"),
        ("#616161", "Graphite"),
        ("#a79b8e", "Birch"),
        ("#ad1457", "Radicchio"),
        ("#d81b60", "Cherry Blossom"),
        ("#8e24aa", "Grape"),
        ("#9e69af", "Amethyst"),
    ],
)
def test_calendar_label_swatches_resolve_to_their_names(calendar_day_module, background, name):
    color = calendar_day_module.resolve_event_color(
        {"eventLabelId": "label"}, [{"id": "label", "backgroundColor": background}], [], {}
    )
    assert color["name"] == name
    assert color["background"] == background


def test_legacy_event_color_is_used_when_no_event_label_exists(calendar_day_module):
    event = {"colorId": "3"}
    palette = {"3": {"background": "#dbadff", "foreground": "#1d1d1d"}}
    color = calendar_day_module.resolve_event_color(event, [], palette, {})
    assert color == {
        "name": None,
        "background": "#dbadff",
        "foreground": "#1d1d1d",
        "source": "legacy_event_color",
    }


def test_events_without_overrides_inherit_primary_calendar_color(calendar_day_module):
    color = calendar_day_module.resolve_event_color(
        {}, [], {}, {"backgroundColor": "#ff9500", "foregroundColor": "#000000"}
    )
    assert color == {
        "name": None,
        "background": "#ff9500",
        "foreground": "#000000",
        "source": "calendar_default",
    }


def test_fetch_uses_only_primary_calendar_and_follows_all_event_pages(calendar_day_module):
    class Request:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class StaticResource:
        def __init__(self, result):
            self.result = result

        def get(self, **_kwargs):
            return Request(self.result)

    class EventResource:
        def __init__(self):
            self.calls = []

        def list(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("pageToken") == "page-2":
                return Request({"items": [{"summary": "Second"}]})
            return Request({"items": [{"summary": "First"}], "nextPageToken": "page-2"})

    class Service:
        def __init__(self):
            self.event_resource = EventResource()

        def calendars(self):
            return StaticResource({"summary": "Theodore", "labelProperties": {"eventLabels": []}})

        def calendarList(self):
            return StaticResource({"backgroundColor": "#ff9500", "foregroundColor": "#000000"})

        def colors(self):
            return StaticResource({"event": {}, "calendar": {}})

        def events(self):
            return self.event_resource

    service = Service()
    result = calendar_day_module.fetch_calendar_day(service, date(2026, 10, 12))
    assert result["calendar"] == "Theodore"
    assert [event["summary"] for event in result["events"]] == ["First", "Second"]
    assert len(service.event_resource.calls) == 2
    assert all(call["calendarId"] == "primary" for call in service.event_resource.calls)
    assert service.event_resource.calls[0]["timeMin"] == "2026-10-11T16:00:00+00:00"
    assert service.event_resource.calls[0]["timeMax"] == "2026-10-12T16:00:00+00:00"
    assert service.event_resource.calls[1]["pageToken"] == "page-2"
