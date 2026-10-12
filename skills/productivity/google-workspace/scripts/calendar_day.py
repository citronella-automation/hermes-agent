#!/usr/bin/env python3
"""Fetch one date from the user's primary Google Calendar, in local time."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Singapore"
KNOWN_EVENT_LABEL_NAMES = {
    "#795548": "Cocoa",
    "#e67c73": "Flamingo",
    "#d50000": "Tomato",
    "#f4511e": "Tangerine",
    "#ef6c00": "Pumpkin",
    "#f09300": "Mango",
    "#009688": "Eucalyptus",
    "#0b8043": "Basil",
    "#7cb342": "Pistachio",
    "#c0ca33": "Avocado",
    "#e4c441": "Citron",
    "#f6bf26": "Banana",
    "#33b679": "Sage",
    "#039be5": "Peacock",
    "#4285f4": "Cobalt",
    "#3f51b5": "Blueberry",
    "#7986cb": "Lavender",
    "#b39ddb": "Wisteria",
    "#616161": "Graphite",
    "#a79b8e": "Birch",
    "#ad1457": "Radicchio",
    "#d81b60": "Cherry Blossom",
    "#8e24aa": "Grape",
    "#9e69af": "Amethyst",
}


def day_window(target_day: date, timezone_name: str = DEFAULT_TIMEZONE) -> tuple[datetime, datetime]:
    """Return the half-open local-day interval [midnight, next midnight)."""
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(target_day, time.min, tzinfo=zone)
    end = datetime.combine(date.fromordinal(target_day.toordinal() + 1), time.min, tzinfo=zone)
    return start, end


def _color_name(background: str | None) -> str | None:
    return KNOWN_EVENT_LABEL_NAMES.get(background.lower()) if background else None


def resolve_event_color(
    event: dict[str, Any],
    event_labels: list[dict[str, Any]],
    legacy_event_palette: dict[str, dict[str, str]],
    calendar_view: dict[str, Any],
    calendar_palette: dict[str, dict[str, str]] | None = None,
) -> dict[str, str | None]:
    """Resolve modern event labels first, then legacy event/calendar colors."""
    label_id = event.get("eventLabelId")
    if label_id:
        label = next((entry for entry in event_labels if entry.get("id") == label_id), None)
        if label:
            background = label.get("backgroundColor")
            foreground = label.get("foregroundColor")
            return {
                "name": _color_name(background),
                "background": background,
                "foreground": foreground,
                "source": "event_label",
            }

    color_id = event.get("colorId")
    if color_id and color_id in legacy_event_palette:
        color = legacy_event_palette[color_id]
        background = color.get("background")
        return {
            "name": _color_name(background),
            "background": background,
            "foreground": color.get("foreground"),
            "source": "legacy_event_color",
        }

    background = calendar_view.get("backgroundColor")
    foreground = calendar_view.get("foregroundColor")
    if not background and calendar_palette:
        calendar_color_id = calendar_view.get("colorId")
        color = calendar_palette.get(calendar_color_id, {}) if calendar_color_id else {}
        background = color.get("background")
        foreground = color.get("foreground")
    return {
        "name": _color_name(background),
        "background": background,
        "foreground": foreground,
        "source": "calendar_default",
    }


def fetch_calendar_day(
    service: Any,
    target_day: date,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Fetch all non-cancelled events on the primary calendar for one local day."""
    start, end = day_window(target_day, timezone_name)
    start_utc = start.astimezone(UTC).isoformat()
    end_utc = end.astimezone(UTC).isoformat()

    calendar = service.calendars().get(calendarId="primary").execute()
    calendar_view = service.calendarList().get(calendarId="primary").execute()
    colors = service.colors().get().execute()
    event_labels = calendar.get("labelProperties", {}).get("eventLabels", [])

    events: list[dict[str, Any]] = []
    page_token = None
    while True:
        page = service.events().list(
            calendarId="primary",
            timeMin=start_utc,
            timeMax=end_utc,
            timeZone=timezone_name,
            maxResults=2500,
            singleEvents=True,
            orderBy="startTime",
            showDeleted=False,
            pageToken=page_token,
        ).execute()
        for event in page.get("items", []):
            if event.get("status") == "cancelled":
                continue
            if any(
                attendee.get("self") and attendee.get("responseStatus") == "declined"
                for attendee in event.get("attendees", [])
            ):
                continue
            events.append(
                {
                    "summary": event.get("summary", "(no title)"),
                    "start": event.get("start", {}).get("dateTime")
                    or event.get("start", {}).get("date", ""),
                    "end": event.get("end", {}).get("dateTime")
                    or event.get("end", {}).get("date", ""),
                    "location": event.get("location"),
                    "status": event.get("status", "confirmed"),
                    "htmlLink": event.get("htmlLink"),
                    "eventLabelId": event.get("eventLabelId"),
                    "colorId": event.get("colorId"),
                    "color": resolve_event_color(
                        event,
                        event_labels,
                        colors.get("event", {}),
                        calendar_view,
                        colors.get("calendar", {}),
                    ),
                }
            )
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    return {
        "date": target_day.isoformat(),
        "timezone": timezone_name,
        "calendar": calendar.get("summary", "primary"),
        "calendar_id": "primary",
        "window_local": [start.isoformat(), end.isoformat()],
        "window_utc": [start_utc, end_utc],
        "events": events,
    }


def _build_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from _hermes_home import get_hermes_home

    token_path = get_hermes_home() / "google_token.json"
    if not token_path.exists():
        raise RuntimeError(f"Google OAuth token not found at {token_path}; run the Workspace setup first")
    credentials = Credentials.from_authorized_user_file(str(token_path))
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", type=_parse_date, help="local calendar date (YYYY-MM-DD)")
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"IANA timezone for the day window (default: {DEFAULT_TIMEZONE})",
    )
    args = parser.parse_args()
    try:
        ZoneInfo(args.timezone)
        result = fetch_calendar_day(_build_service(), args.date, args.timezone)
    except ZoneInfoNotFoundError:
        parser.error(f"unknown IANA timezone: {args.timezone}")
    except Exception as exc:
        parser.exit(1, f"Calendar fetch failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
