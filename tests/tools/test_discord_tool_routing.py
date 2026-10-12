"""Independent Discord REST writes obey the active profile's output boundary."""
import json
from unittest.mock import MagicMock

import pytest
from tools import discord_tool


@pytest.mark.parametrize("action", ["create_thread", "pin_message", "unpin_message", "delete_message"])
def test_top_level_discord_routing_blocks_writes_before_rest(monkeypatch, action):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"discord": {
        "assistant_input_channel": "100", "assistant_output_channel": "300"}})
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock(return_value={"id": "999"})
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    handler = discord_tool.discord_core if action == "create_thread" else discord_tool.discord_admin_handler
    result = json.loads(handler(action=action, channel_id="100", message_id="777", name="task"))
    assert "error" in result, result
    request.assert_not_called()


@pytest.mark.parametrize("config", [{}, {"assistant_input_channel": "100"}, {"assistant_output_channel": "300"}])
@pytest.mark.parametrize("action", ["create_thread", "pin_message", "unpin_message", "delete_message"])
def test_disabled_or_incomplete_routing_preserves_rest(monkeypatch, config, action):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"platforms": {"discord": config}})
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock(return_value={"id": "999"})
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    handler = discord_tool.discord_core if action == "create_thread" else discord_tool.discord_admin_handler
    result = json.loads(handler(action=action, channel_id="400", message_id="777", name="task"))
    assert result["success"]
    request.assert_called_once()


@pytest.mark.parametrize("nested", [
    {"assistant_input_channel": "200", "assistant_output_channel": "400"},
    {"extra": {"assistant_input_channel": "200", "assistant_output_channel": "400"}},
])
@pytest.mark.parametrize("channel_id", ["100", "300", "400"])
def test_routing_config_conflicts_follow_gateway_precedence(monkeypatch, nested, channel_id):
    config = {"discord": {"assistant_input_channel": "100", "assistant_output_channel": "300"},
              "platforms": {"discord": nested}}
    # Root adapter keys outrank nested direct keys; explicit nested extra
    # outranks the root block (gateway.config_loader._authored_wins).
    sink = "400" if "extra" in nested else "300"
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock()
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    result = json.loads(discord_tool.discord_admin_handler(
        action="pin_message", channel_id=channel_id, message_id="777"))
    assert ("error" in result) is (channel_id != sink), result
    assert request.call_count == (1 if channel_id == sink else 0)
    assert config["discord"]["assistant_output_channel"] == "300"


@pytest.mark.parametrize("action", ["pin_message", "unpin_message", "delete_message"])
def test_message_mutations_in_sink_keep_original_message_id(monkeypatch, action):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"platforms": {"discord": {
        "assistant_input_channel": "100", "assistant_output_channel": "300"}}})
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock()
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    assert json.loads(discord_tool.discord_admin_handler(
        action=action, channel_id="300", message_id="777"))["success"]
    assert "/channels/300/" in request.call_args.args[1]
    assert request.call_args.args[1].endswith("/777")


def test_routing_load_failure_blocks_writes(monkeypatch):
    def fail():
        raise OSError("unreadable config")
    monkeypatch.setattr("hermes_cli.config.load_config", fail)
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock()
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    assert "error" in json.loads(discord_tool.discord_core(
        action="create_thread", channel_id="300", name="task"))
    request.assert_not_called()


def test_routing_uses_context_local_profile_without_cached_policy(monkeypatch, tmp_path):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    homes = [tmp_path / "routed", tmp_path / "legacy"]
    for home, config in zip(homes, [{"platforms": {"discord": {"extra": {
            "assistant_input_channel": "100", "assistant_output_channel": "300"}}}}, {}]):
        home.mkdir()
        (home / "config.yaml").write_text(json.dumps(config))
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock(return_value={"id": "999"})
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    for home, blocked in [(homes[0], True), (homes[1], False), (homes[0], True)]:
        token = set_hermes_home_override(home)
        try:
            result = json.loads(discord_tool.discord_core(action="create_thread", channel_id="300", name="task"))
            assert ("error" in result) is blocked, result
        finally:
            reset_hermes_home_override(token)
    request.assert_called_once()

@pytest.mark.parametrize("message_id", ["", "777"])
def test_routed_profile_blocks_thread_creation_before_rest(monkeypatch, message_id):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {
        "platforms": {"discord": {"assistant_input_channel": "100", "assistant_output_channel": "300"}}})
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock(return_value={"id": "999"})
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    result = json.loads(discord_tool.discord_core(action="create_thread", channel_id="300",
                                                 name="task", message_id=message_id))
    assert "error" in result, result
    assert "routing" in result["error"].lower()
    request.assert_not_called()


@pytest.mark.parametrize("action", ["pin_message", "unpin_message", "delete_message"])
@pytest.mark.parametrize("channel_id", ["100", "400", "301"])
def test_message_mutations_cannot_write_outside_sink(monkeypatch, action, channel_id):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {
        "platforms": {"discord": {"extra": {
            "assistant_input_channel": "100", "assistant_output_channel": "300"}}}})
    monkeypatch.setattr(discord_tool, "_get_bot_token", lambda: "fake")
    request = MagicMock()
    monkeypatch.setattr(discord_tool, "_discord_request", request)
    result = json.loads(discord_tool.discord_admin_handler(
        action=action, channel_id=channel_id, message_id="777"))
    assert "error" in result, result
    request.assert_not_called()
