"""Read-only Discord utility behavior."""

import json
from pathlib import Path
import stat

from tools import discord_readonly


def test_script_launcher_routes_through_hermes_managed_environment():
    shebang = Path(discord_readonly.__file__).read_text().splitlines()[0]
    assert shebang.startswith("#!/usr/bin/env -S bash -c ")
    assert "readlink -f \"$0\"" in shebang
    assert "../scripts/run-in-hermes-env" in shebang


def test_recent_messages_filters_explicit_channel_to_relative_window(monkeypatch):
    monkeypatch.setattr(discord_readonly, "_get_bot_token", lambda: "profile-token")
    calls = []
    monkeypatch.setattr(discord_readonly, "_discord_request", lambda *a, **kw: calls.append((a, kw)) or [
        {"id": "new", "content": "in range", "timestamp": "2026-05-01T11:00:00+00:00", "author": {}},
        {"id": "old", "content": "out", "timestamp": "2026-04-30T10:00:00+00:00", "author": {}},
    ])
    result = json.loads(discord_readonly.fetch_recent_messages("1517078886049910835", hours=24, now="2026-05-01T12:00:00+00:00"))
    assert [m["id"] for m in result["messages"]] == ["new"]
    assert calls[0][0][1] == "/channels/1517078886049910835/messages"
    assert calls[0][0][0] == "GET"


def test_channel_inventory_only_includes_channels_with_view_and_history(monkeypatch):
    monkeypatch.setattr(discord_readonly, "_get_bot_token", lambda: "profile-token")
    monkeypatch.setattr(discord_readonly, "_discord_request", lambda method, path, token, **kw: {
        "/users/@me": {"id": "bot"},
        "/guilds/g/channels": [
            {"id": "1517078886049910835", "name": "disorganized-todo", "type": 0},
            {"id": "voice", "name": "General", "type": 2},
            {"id": "category", "name": "tasks", "type": 4},
            {"id": "hidden", "name": "hidden", "type": 0, "permission_overwrites": [
                {"id": "g", "type": 0, "deny": "65536", "allow": "0"},
            ]},
        ],
        "/guilds/g/roles": [{"id": "g", "permissions": "66560"}],
        "/guilds/g/members/bot": {"roles": []},
    }[path])
    inventory = json.loads(discord_readonly.list_readable_channels("g", refresh=True))
    assert inventory["channels"] == [{"id": "1517078886049910835", "name": "disorganized-todo", "type": 0}]
    assert inventory["count"] == 1


def test_inventory_cache_obeys_ttl_and_refresh(monkeypatch):
    monkeypatch.setattr(discord_readonly, "_get_bot_token", lambda: "profile-token")
    calls = []
    def request(method, path, token, **kwargs):
        calls.append(path)
        if path == "/users/@me": return {"id": "bot"}
        if path == "/guilds/g/channels": return []
        if path == "/guilds/g/roles": return [{"id": "g", "permissions": "68608"}]
        return {"roles": []}
    monkeypatch.setattr(discord_readonly, "_discord_request", request)
    monkeypatch.setattr(discord_readonly, "time", type("T", (), {"time": staticmethod(lambda: 100)}))
    discord_readonly._reset_inventory_cache()
    discord_readonly.list_readable_channels("g", ttl_seconds=60)
    discord_readonly.list_readable_channels("g", ttl_seconds=60)
    assert calls.count("/guilds/g/channels") == 1
    discord_readonly.list_readable_channels("g", ttl_seconds=60, refresh=True)
    assert calls.count("/guilds/g/channels") == 2


def test_inventory_cache_persists_across_process_cache_resets(monkeypatch, tmp_path):
    monkeypatch.setattr(discord_readonly, "_get_bot_token", lambda: "profile-token")
    calls = []
    def request(method, path, token, **kwargs):
        calls.append(path)
        if path == "/users/@me": return {"id": "bot"}
        if path == "/guilds/g/channels":
            return [{"id": "1517078886049910835", "name": "disorganized-todo", "type": 0}]
        if path == "/guilds/g/roles": return [{"id": "g", "permissions": "66560"}]
        return {"roles": []}
    monkeypatch.setattr(discord_readonly, "_discord_request", request)
    cache_path = tmp_path / "channels.json"
    discord_readonly._reset_inventory_cache()
    first = json.loads(discord_readonly.list_readable_channels("g", cache_path=cache_path))
    assert first["cached"] is False
    assert cache_path.exists()
    initial_call_count = len(calls)

    discord_readonly._reset_inventory_cache()
    second = json.loads(discord_readonly.list_readable_channels("g", cache_path=cache_path))
    assert second["cached"] is True
    assert second["channels"] == first["channels"]
    assert len(calls) == initial_call_count
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600

    monkeypatch.setattr(discord_readonly, "_get_bot_token", lambda: "rotated-token")
    discord_readonly._reset_inventory_cache()
    third = json.loads(discord_readonly.list_readable_channels("g", cache_path=cache_path))
    assert third["cached"] is False
    assert len(calls) > initial_call_count


def test_default_inventory_cache_uses_active_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setattr(discord_readonly, "get_hermes_home", lambda: tmp_path, raising=False)
    assert discord_readonly._default_cache_path() == tmp_path / "cache" / "discord_readonly" / "channels.json"


def test_cli_fetches_messages_for_named_readable_channel(monkeypatch, capsys):
    monkeypatch.setattr(discord_readonly, "resolve_guild_id", lambda guild_name: "guild-id")
    monkeypatch.setattr(
        discord_readonly,
        "list_readable_channels",
        lambda guild_id, **kwargs: json.dumps({"channels": [
            {"id": "1517078886049910835", "name": "disorganized-todo", "type": 0}
        ]}),
    )
    monkeypatch.setattr(
        discord_readonly,
        "fetch_recent_messages",
        lambda channel_id, **kwargs: json.dumps({"channel_id": channel_id, "messages": [], "count": 0}),
    )
    assert discord_readonly.main(["messages", "--channel", "disorganized-todo", "--hours", "24"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["channel_id"] == "1517078886049910835"


def test_cli_lists_readable_channels(monkeypatch, capsys):
    monkeypatch.setattr(discord_readonly, "resolve_guild_id", lambda guild_name: "guild-id")
    monkeypatch.setattr(
        discord_readonly,
        "list_readable_channels",
        lambda guild_id, **kwargs: json.dumps({"channels": [], "count": 0}),
    )
    assert discord_readonly.main(["channels"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0
