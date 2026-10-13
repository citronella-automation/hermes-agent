#!/usr/bin/env python3
"""Create or patch an event on the user's Google Calendar."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_api

DEFAULT_CALENDAR = "primary"


def aware_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO 8601 datetime with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone offset or Z")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Create or edit a Google Calendar event (no delete or recurrence).")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create", help="Create an event")
    create.add_argument("--summary", required=True)
    create.add_argument("--start", required=True, type=aware_datetime)
    create.add_argument("--end", required=True, type=aware_datetime)
    create.add_argument("--location")
    create.add_argument("--description")
    create.add_argument("--color-id", help="Legacy Google Calendar color ID (social purple: 3)")
    update = sub.add_parser("update", aliases=["edit"], help="Patch an event by ID")
    update.add_argument("event_id")
    update.add_argument("--summary")
    update.add_argument("--start", type=aware_datetime)
    update.add_argument("--end", type=aware_datetime)
    update.add_argument("--location")
    update.add_argument("--description")
    update.add_argument("--color-id", help="Legacy Google Calendar color ID (social purple: 3)")
    return parser.parse_args(argv)


def build_service():
    return google_api.build_service("calendar", "v3")


def run(args):
    service = build_service()
    if args.action == "create":
        body = {"summary": args.summary,
                "start": {"dateTime": args.start},
                "end": {"dateTime": args.end}}
        for key in ("location", "description", "color_id"):
            value = getattr(args, key, None)
            if value is not None:
                body["colorId" if key == "color_id" else key] = value
        result = service.events().insert(calendarId=DEFAULT_CALENDAR, body=body).execute()
        event_id = result.get("id")
        if not event_id:
            raise SystemExit("Calendar API did not return an event ID")
        result = service.events().get(calendarId=DEFAULT_CALENDAR, eventId=event_id).execute()
        output = {"status": "created", "id": event_id, "summary": result.get("summary", "")}
    else:
        body = {}
        for key in ("summary", "location", "description"):
            value = getattr(args, key)
            if value is not None:
                body[key] = value
        for key in ("start", "end"):
            value = getattr(args, key)
            if value is not None:
                body[key] = {"dateTime": value}
        if args.color_id is not None:
            body["colorId"] = args.color_id
        if not body:
            raise SystemExit("update requires at least one field to patch")
        service.events().patch(calendarId=DEFAULT_CALENDAR, eventId=args.event_id, body=body).execute()
        result = service.events().get(calendarId=DEFAULT_CALENDAR, eventId=args.event_id).execute()
        output = {"status": "updated", "id": result.get("id", args.event_id), "summary": result.get("summary", "")}
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run(parse_args())
