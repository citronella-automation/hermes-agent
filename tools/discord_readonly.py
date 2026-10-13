#!/usr/bin/env -S bash -c 'p=$(readlink -f "$0"); exec "$BASH" "${p%/*}/../scripts/run-in-hermes-env" python3 "$p" "$@"'
"""Read-only Discord message and permission-filtered channel inventory helpers."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.secret_scope import load_env_file, reset_secret_scope, set_secret_scope
from hermes_constants import get_hermes_home
from tools.discord_tool import _discord_request, _get_bot_token, _message_summary

_VIEW_CHANNEL = 1 << 10
_READ_MESSAGE_HISTORY = 1 << 16
_MESSAGE_CHANNEL_TYPES = {0, 5, 10, 11, 12, 15, 16}
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _token() -> str:
    token = _get_bot_token()
    if not token:
        raise RuntimeError("Discord bot token is unavailable in the active profile scope")
    return token


def _permissions(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _default_cache_path() -> Path:
    return Path(get_hermes_home()) / "cache" / "discord_readonly" / "channels.json"


def _can_read(channel: dict[str, Any], roles: dict[str, int], member: dict[str, Any], guild_id: str) -> bool:
    everyone = roles.get(guild_id, 0)
    role_ids = set(member.get("roles", [])) | {guild_id}
    perms = everyone
    for role_id in role_ids - {guild_id}:
        perms |= roles.get(role_id, 0)
    if perms & (1 << 3):
        return True
    overwrites = channel.get("permission_overwrites", [])
    everyone_overwrite = next((o for o in overwrites if o.get("id") == guild_id and o.get("type") == 0), None)
    if everyone_overwrite:
        perms &= ~_permissions(everyone_overwrite.get("deny"))
        perms |= _permissions(everyone_overwrite.get("allow"))
    role_denies = role_allows = 0
    for overwrite in overwrites:
        if overwrite.get("type") == 0 and overwrite.get("id") in role_ids - {guild_id}:
            role_denies |= _permissions(overwrite.get("deny"))
            role_allows |= _permissions(overwrite.get("allow"))
    perms = (perms & ~role_denies) | role_allows
    for overwrite in overwrites:
        if overwrite.get("type") == 1 and overwrite.get("id") == member.get("user", {}).get("id"):
            perms &= ~_permissions(overwrite.get("deny"))
            perms |= _permissions(overwrite.get("allow"))
    required = _VIEW_CHANNEL | _READ_MESSAGE_HISTORY
    return perms & required == required


def resolve_guild_id(guild_name: str | None = None) -> str:
    """Resolve a guild ID by exact name; when omitted require exactly one guild."""
    guilds = _discord_request("GET", "/users/@me/guilds", _token())
    if guild_name:
        matches = [g for g in guilds if g.get("name", "").casefold() == guild_name.casefold()]
    else:
        matches = guilds
    if len(matches) != 1:
        raise ValueError("Specify --guild by exact name; matching guild count: " + str(len(matches)))
    return str(matches[0]["id"])


def _reset_inventory_cache() -> None:
    with _cache_lock:
        _cache.clear()


def list_readable_channels(guild_id: str, *, refresh: bool = False, ttl_seconds: int = 86400,
                           cache_path: str | Path | None = None) -> str:
    """List message channels with both read permissions; cache persistently for 24h by default."""
    token = _token()
    key = (hashlib.sha256(token.encode("utf-8")).hexdigest(), str(guild_id))
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if not refresh and cached and now - cached[0] < max(0, ttl_seconds):
            return json.dumps({"channels": cached[1], "count": len(cached[1]), "cached": True})
        if not refresh:
            try:
                stored = json.loads(path.read_text())
                entry = stored.get(f"{guild_id}:{key[0]}")
                if entry and 0 <= now - float(entry["timestamp"]) < max(0, ttl_seconds):
                    channels = entry["channels"]
                    _cache[key] = (float(entry["timestamp"]), channels)
                    return json.dumps({"channels": channels, "count": len(channels), "cached": True})
            except (OSError, ValueError, KeyError, TypeError):
                pass
    bot = _discord_request("GET", "/users/@me", token)
    raw_channels = _discord_request("GET", f"/guilds/{guild_id}/channels", token)
    raw_roles = _discord_request("GET", f"/guilds/{guild_id}/roles", token)
    member = _discord_request("GET", f"/guilds/{guild_id}/members/{bot['id']}", token)
    roles = {str(role["id"]): _permissions(role.get("permissions")) for role in raw_roles}
    channels = [
        {"id": str(ch["id"]), "name": ch.get("name", ""), "type": ch.get("type")}
        for ch in raw_channels
        if ch.get("type") in _MESSAGE_CHANNEL_TYPES and _can_read(ch, roles, member, str(guild_id))
    ]
    with _cache_lock:
        _cache[key] = (now, channels)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path == _default_cache_path():
            path.parent.chmod(0o700)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        stored[f"{guild_id}:{key[0]}"] = {"timestamp": now, "channels": channels}
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(json.dumps(stored, indent=2))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    except OSError:
        pass
    return json.dumps({"channels": channels, "count": len(channels), "cached": False})


def fetch_recent_messages(channel_id: str, *, hours: float = 24, limit: int = 100,
                          now: str | datetime | None = None) -> str:
    """Fetch messages from one explicit channel within a relative time window (maximum 100)."""
    if hours <= 0:
        raise ValueError("hours must be greater than zero")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    end = datetime.fromisoformat(now) if isinstance(now, str) else now
    end = end or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    start = end - timedelta(hours=hours)
    messages = _discord_request("GET", f"/channels/{channel_id}/messages", _token(), params={"limit": str(limit)})
    selected = []
    for message in messages:
        stamp = datetime.fromisoformat(message["timestamp"])
        if start <= stamp <= end:
            selected.append(_message_summary(message))
    return json.dumps({"channel_id": str(channel_id), "since": start.isoformat(), "messages": selected, "count": len(selected)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Discord channel inventory and message reader")
    parser.add_argument("command", choices=("channels", "messages"))
    parser.add_argument("--guild", help="Exact guild name (omit only when the bot belongs to one guild)")
    parser.add_argument("--channel", help="Exact channel name or channel ID (required for messages)")
    parser.add_argument("--hours", type=float, default=24, help="Relative message window in hours")
    parser.add_argument("--limit", type=int, default=100, help="Message page size, 1-100")
    parser.add_argument("--refresh", action="store_true", help="Refresh persistent channel inventory")
    parser.add_argument("--cache-ttl", type=int, default=86400, help="Inventory cache TTL in seconds")
    parser.add_argument("--cache-path", type=Path, default=_default_cache_path())
    args = parser.parse_args(argv)
    secret_scope_token = None
    try:
        hermes_home = Path(get_hermes_home())
        secret_scope_token = set_secret_scope(
            load_env_file(hermes_home / ".env"), profile_home=str(hermes_home))
        guild_id = resolve_guild_id(args.guild)
        inventory = json.loads(list_readable_channels(guild_id, refresh=args.refresh,
                                                       ttl_seconds=args.cache_ttl, cache_path=args.cache_path))
        if args.command == "channels":
            print(json.dumps(inventory, indent=2))
            return 0
        if not args.channel:
            parser.error("messages requires --channel")
        matches = [c for c in inventory["channels"] if args.channel in (c["id"], c["name"])]
        if len(matches) != 1:
            raise ValueError("--channel must uniquely match a readable channel name or ID")
        print(fetch_recent_messages(matches[0]["id"], hours=args.hours, limit=args.limit))
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    finally:
        if secret_scope_token is not None:
            reset_secret_scope(secret_scope_token)


if __name__ == "__main__":
    raise SystemExit(main())
