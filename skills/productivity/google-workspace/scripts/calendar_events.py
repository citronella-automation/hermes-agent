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
CATEGORY_LABEL_NAMES = {
    "cs3210": "Cobalt", "cs1231s": "Pistachio", "admin": "Birch",
    "research": "Eucalyptus", "lab": None, "social": "Grape",
    "health": "Tomato", "extracurricular": "Cocoa",
}
# User-authorized legacy fallback only; Cocoa has no equivalent.
CATEGORY_COLOR_IDS = {
    "cs3210": "9", "cs1231s": "10", "admin": "8", "research": "2",
    "lab": "10", "social": "3", "health": "11",
}


def aware_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162 — preserve UTC normalization
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

    update = sub.add_parser("update", aliases=["edit"], help="Patch an event by ID")
    update.add_argument("event_id")
    update.add_argument("--summary")
    update.add_argument("--start", type=aware_datetime)
    update.add_argument("--end", type=aware_datetime)
    update.add_argument("--location")
    update.add_argument("--description")
    for command in (create, update):
        color = command.add_mutually_exclusive_group()
        color.add_argument("--color-id", help="Explicit legacy color ID; use instead of --category to override its mapping")
        color.add_argument("--category", choices=CATEGORY_LABEL_NAMES, help="Exact modern label preferred; reported legacy fallback when unavailable")
        color.add_argument("--label-name", help="Exact calendar label name (no fallback)")
        color.add_argument("--event-label-id", help="Existing calendar label ID (no fallback)")
    args = parser.parse_args(argv)
    return args


def build_service():
    return google_api.build_service("calendar", "v3")


def execute_event(request, modern=False):
    # Bundled discovery lacks eventLabelVersion. HttpRequest retains SDK auth,
    # serialization and error handling; append Google's documented query flag.
    if modern:
        request.uri += ("&" if "?" in request.uri else "?") + "eventLabelVersion=1"
    return request.execute()


def resolve_label(service, args):
    if not (args.category or args.label_name or args.event_label_id):
        return None, None
    metadata = service.calendars().get(calendarId=DEFAULT_CALENDAR).execute()
    labels = (metadata.get("labelProperties") or {}).get("eventLabels") or []
    name = args.label_name or CATEGORY_LABEL_NAMES.get(args.category)
    matches = [label for label in labels if
               (label.get("id") == args.event_label_id if args.event_label_id
                else name is not None and label.get("name") == name)]
    if len(matches) > 1:
        raise SystemExit("Ambiguous calendar label; specify --event-label-id")
    if matches and matches[0].get("id"):
        return matches[0]["id"], None
    if args.category in CATEGORY_COLOR_IDS:
        args.color_id = CATEGORY_COLOR_IDS[args.category]
        return None, f"Modern label {name!r} unavailable/unresolved; legacy colorId {args.color_id} fallback"
    raise SystemExit("Requested modern label missing/unresolved; no legacy equivalent (Cocoa requires a verified label ID)")


def run(args):
    service = build_service()
    label_id, fallback = resolve_label(service, args)
    modern = label_id is not None
    if args.action == "create":
        body = {"summary": args.summary,
                "start": {"dateTime": args.start},
                "end": {"dateTime": args.end}}
        for key in ("location", "description", "color_id"):
            value = getattr(args, key, None)
            if value is not None:
                body["colorId" if key == "color_id" else key] = value
        if modern:
            body["eventLabelId"] = label_id
        result = execute_event(service.events().insert(calendarId=DEFAULT_CALENDAR, body=body), modern)
        event_id = result.get("id")
        if not event_id:
            raise SystemExit("Calendar API did not return an event ID")
        result = execute_event(service.events().get(calendarId=DEFAULT_CALENDAR, eventId=event_id), modern)
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
        if modern:
            body["eventLabelId"] = label_id
        if not body:
            raise SystemExit("update requires at least one field to patch")
        execute_event(service.events().patch(calendarId=DEFAULT_CALENDAR, eventId=args.event_id, body=body), modern)
        result = execute_event(service.events().get(calendarId=DEFAULT_CALENDAR, eventId=args.event_id), modern)
        output = {"status": "updated", "id": result.get("id", args.event_id), "summary": result.get("summary", "")}
    if modern:
        if result.get("eventLabelId") != label_id:
            raise SystemExit("Event written but modern label readback mismatch; inspect event before retrying")
        output["eventLabelId"] = result["eventLabelId"]
    if fallback:
        output["colorFallback"] = fallback
        output["colorId"] = args.color_id
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run(parse_args())
