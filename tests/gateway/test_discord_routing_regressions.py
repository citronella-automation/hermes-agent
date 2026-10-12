"""Configured assistant routing must cover recovery, slash and REST paths."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from tests.gateway.test_discord_free_response import (
    adapter, FakeTextChannel, FakeThread, FakeDMChannel, make_message,
    DiscordAdapter, PlatformConfig, discord_platform,
)
from plugins.platforms.discord.assistant_channels import DiscordAssistantMonitor, DiscordAssistantChannelPolicy


@pytest.mark.asyncio
async def test_simple_slash_success_acknowledges_sink_and_deletes_placeholder(adapter):
    enable(adapter)
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter._defer_unless_expired = AsyncMock(return_value=True)
    adapter._build_slash_event = MagicMock(return_value=object())
    adapter.handle_message = AsyncMock()
    adapter.send = AsyncMock()
    interaction = SimpleNamespace(channel_id=100, edit_original_response=AsyncMock(),
                                  delete_original_response=AsyncMock())
    await adapter._run_simple_slash(interaction, "/reset", "Acknowledged")
    adapter.send.assert_awaited_once_with("300", "Acknowledged")
    interaction.edit_original_response.assert_not_awaited()
    interaction.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("routed,followup,expired,send_fails", [
    (False, "Acknowledged", False, False), (False, None, False, False),
    (True, None, False, False), (True, "Acknowledged", True, False),
    (True, "Acknowledged", False, True),
])
async def test_simple_slash_cleanup_and_legacy(adapter, routed, followup, expired, send_fails):
    if routed:
        enable(adapter)
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter._defer_unless_expired = AsyncMock(return_value=not expired)
    adapter._build_slash_event = MagicMock(return_value=object())
    adapter.handle_message = AsyncMock()
    adapter.send = AsyncMock(side_effect=RuntimeError("sink unavailable") if send_fails else None)
    interaction = SimpleNamespace(channel_id=100, edit_original_response=AsyncMock(),
                                  delete_original_response=AsyncMock())
    await adapter._run_simple_slash(interaction, "/reset", followup)
    adapter.handle_message.assert_awaited_once()
    assert interaction.edit_original_response.await_count == int(not routed and bool(followup) and not expired)
    assert interaction.delete_original_response.await_count == int(not expired and (routed or not followup))
    assert adapter.send.await_count == int(routed and bool(followup) and not expired)


def enable(adapter):
    adapter._assistant_monitor = DiscordAssistantMonitor(DiscordAssistantChannelPolicy(
        input_channel_id="100", context_channel_ids=["200", "300"], output_channel_id="300"))
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [200, 400, 101])
async def test_recovery_never_dispatches_other_guild_channels(adapter, channel_id):
    enable(adapter)
    adapter._discord_message_admission = MagicMock(return_value=(True, False))
    adapter._handle_message = AsyncMock(return_value=True)
    message = make_message(channel=FakeTextChannel(channel_id), content="<@999> reference")
    message.guild = message.channel.guild
    await adapter._dispatch_recovered_message(message)
    adapter._handle_message.assert_not_awaited()
    if channel_id == 200:
        assert "reference" in adapter._assistant_monitor.context_for_input("100")


@pytest.mark.parametrize("channel_id", [200, 400, 101])
def test_slash_rejects_non_input_channel(adapter, channel_id):
    enable(adapter)
    adapter._is_allowed_user = MagicMock(return_value=True)
    interaction = SimpleNamespace(channel=FakeTextChannel(channel_id), channel_id=channel_id,
                                  user=SimpleNamespace(id=42), guild=SimpleNamespace(id=1))
    assert adapter._evaluate_slash_authorization(interaction)[0] is False


@pytest.mark.asyncio
async def test_configured_input_never_auto_threads(adapter, monkeypatch):
    enable(adapter)
    monkeypatch.setenv("DISCORD_FREE_RESPONSE_AUTO_THREAD", "true")
    adapter._auto_create_thread = AsyncMock(return_value=None)
    assert await adapter._handle_message(make_message(channel=FakeTextChannel(100), content="hello"))
    adapter._auto_create_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_rest_routes_arbitrary_destination(adapter, monkeypatch):
    import aiohttp
    enable(adapter)
    adapter.config.extra.update(assistant_input_channel="100", assistant_output_channel="300")
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"id": "555"})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(aiohttp, "ClientSession", MagicMock(return_value=context))
    monkeypatch.setattr(discord_platform, "_standalone_is_forum", AsyncMock(return_value=False))
    result = await discord_platform._standalone_send(adapter.config, "400", "hello", thread_id="401")
    assert result["success"], result
    assert session.post.call_args.args[0] == "https://discord.com/api/v10/channels/300/messages"
    assert result["chat_id"] == "300"


@pytest.mark.parametrize("chat_id,metadata", [("300", {"thread_id": "401"}), ("401", {"parent_chat_id": "300", "thread_id": "401"})])
def test_sink_still_clears_source_thread_anchors(adapter, chat_id, metadata):
    enable(adapter)
    target, reply, routed_metadata = adapter._route_assistant_output(chat_id, "123", metadata)
    assert target == "300"
    assert reply is None
    assert "thread_id" not in routed_metadata
    assert routed_metadata["reply_to_message_id"] == "123"
    assert metadata["thread_id"] == "401"


@pytest.mark.asyncio
async def test_voice_timeout_notice_goes_to_sink(adapter):
    enable(adapter)
    adapter._voice_text_channels[1] = 100
    adapter.leave_voice_channel = AsyncMock()
    adapter.send = AsyncMock()
    source = SimpleNamespace(send=AsyncMock())
    adapter._client.get_channel = MagicMock(return_value=source)
    await adapter._voice_timeout_handler(1, timeout=1)
    source.send.assert_not_awaited()
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_slash_rejection_is_sent_to_sink_not_interaction(adapter):
    enable(adapter)
    adapter.send = AsyncMock()
    adapter._notify_admin_unauthorized = AsyncMock()
    response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: False)
    interaction = SimpleNamespace(channel_id=200, guild_id=1, user=SimpleNamespace(id=42, name="A"), response=response)
    await adapter._reject_slash(interaction, "/help", reason="channel")
    response.send_message.assert_not_awaited()
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_thread_creation_cannot_emit_outside_sink(adapter):
    enable(adapter)
    adapter._resolve_interaction_channel = AsyncMock(return_value=SimpleNamespace(create_thread=AsyncMock()))
    result = await adapter._create_thread(SimpleNamespace(), name="task", message="starter")
    assert not result.get("success")
    adapter._resolve_interaction_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_reaction_is_suppressed(adapter):
    enable(adapter)
    message = SimpleNamespace(channel=FakeTextChannel(100), add_reaction=AsyncMock())
    assert await adapter._add_reaction(message, "👀") is False
    message.add_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_forum_sink_cannot_create_output_thread(adapter):
    enable(adapter)
    channel = SimpleNamespace(create_thread=AsyncMock())
    adapter._resolve_channel = AsyncMock(return_value=channel)
    adapter._is_forum_parent = MagicMock(return_value=True)
    adapter._record_response_async = AsyncMock(side_effect=lambda reply, result, *args: result)
    result = await adapter.send("400", "hello")
    assert not result.success
    channel.create_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_command_error_goes_to_sink(adapter):
    enable(adapter)
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter._defer_unless_expired = AsyncMock(return_value=True)
    adapter.send = AsyncMock()
    interaction = SimpleNamespace(channel_id=100, followup=SimpleNamespace(send=AsyncMock()))
    await adapter._handle_thread_create_slash(interaction, "task")
    interaction.followup.send.assert_not_awaited()
    adapter.send.assert_awaited_once()


@pytest.mark.parametrize("extra", [{"assistant_input_channel": "100"}, {"assistant_output_channel": "300"}, {}])
def test_incomplete_policy_preserves_legacy_free_channels(monkeypatch, extra):
    monkeypatch.delenv("DISCORD_FREE_RESPONSE_CHANNELS", raising=False)
    routed = DiscordAdapter(PlatformConfig(enabled=True, token="fake", extra=extra))
    assert routed._discord_free_response_channels() == set()
    metadata = {"thread_id": "401"}
    assert routed._route_assistant_output("100", "123", metadata) == ("100", "123", metadata)


@pytest.mark.asyncio
async def test_standalone_forum_sink_fails_closed(adapter, monkeypatch):
    enable(adapter)
    adapter.config.extra.update(assistant_input_channel="100", assistant_output_channel="300")
    monkeypatch.setattr(discord_platform, "_standalone_is_forum", AsyncMock(return_value=True))
    import aiohttp
    session = MagicMock()
    monkeypatch.setattr(aiohttp, "ClientSession", session)
    result = await discord_platform._standalone_send(adapter.config, "400", "hello")
    assert not result.get("success")
    session.assert_not_called()


@pytest.mark.asyncio
async def test_slash_notice_uses_sink_and_acknowledges_without_source_text(adapter):
    enable(adapter)
    adapter.send = AsyncMock()
    interaction = SimpleNamespace(channel_id=100, response=SimpleNamespace(
        is_done=lambda: False, defer=AsyncMock(), send_message=AsyncMock()))
    await adapter._send_slash_notice(interaction, "unknown skill")
    adapter.send.assert_awaited_once_with("300", "unknown skill")
    interaction.response.send_message.assert_not_awaited()
    interaction.response.defer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", ["100", "200", "300", "400", "500", "401"])
async def test_send_any_destination_uses_sink_with_durable_reply_attribution(adapter, chat_id):
    enable(adapter)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=555)))
    adapter._resolve_channel = AsyncMock(return_value=channel)
    adapter._is_forum_parent = MagicMock(return_value=False)
    adapter._record_response_async = AsyncMock(side_effect=lambda reply, result, *args: result)
    metadata = {"thread_id": "401", "notify": True}
    result = await adapter.send(chat_id, "hello", reply_to="123", metadata=metadata)
    assert result.success
    adapter._resolve_channel.assert_awaited_once_with("300")
    assert channel.send.await_args.kwargs["reference"] is None
    recorded = adapter._record_response_async.await_args
    assert recorded.args[0] is None
    assert recorded.args[4]["reply_to_message_id"] == "123"
    assert recorded.args[4]["notify"] is True
    assert metadata == {"thread_id": "401", "notify": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("channel_id", [100, 200, 300, 400, 101])
async def test_guild_ingress_exact_input_including_child_threads(adapter, recovered, channel_id):
    enable(adapter)
    adapter._ready_event.set()
    adapter._discord_message_admission = MagicMock(return_value=(True, False))
    adapter._handle_message = AsyncMock(return_value=True)
    channel = FakeThread(101, parent=FakeTextChannel(100)) if channel_id == 101 else FakeTextChannel(channel_id)
    message = make_message(channel=channel, content="<@999> reference")
    dispatch = adapter._dispatch_recovered_message if recovered else adapter._dispatch_discord_message
    await dispatch(message)
    assert adapter._handle_message.await_count == (1 if channel_id == 100 else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_dm_ingress_preserved_when_sink_enabled(adapter, recovered):
    enable(adapter)
    adapter._ready_event.set()
    adapter._discord_message_admission = MagicMock(return_value=(True, False))
    adapter._handle_message = AsyncMock(return_value=True)
    message = make_message(channel=FakeDMChannel(500), content="private request")
    dispatch = adapter._dispatch_recovered_message if recovered else adapter._dispatch_discord_message
    assert await dispatch(message)
    adapter._handle_message.assert_awaited_once()


@pytest.mark.parametrize("channel", [FakeTextChannel(100), FakeDMChannel(500), FakeThread(101, parent=FakeTextChannel(100))])
def test_slash_exact_input_and_dm_preservation(adapter, channel):
    enable(adapter)
    adapter._is_allowed_user = MagicMock(return_value=True)
    interaction = SimpleNamespace(channel=channel, channel_id=channel.id, user=SimpleNamespace(id=42), guild=getattr(channel, "guild", None))
    assert adapter._evaluate_slash_authorization(interaction)[0] is (channel.id != 101)


@pytest.mark.asyncio
async def test_tts_does_not_bypass_sink_via_active_voice_channel(adapter):
    enable(adapter)
    adapter._voice_text_channels[1] = 100
    adapter.is_in_voice_channel = MagicMock(return_value=True)
    adapter.play_in_voice_channel = AsyncMock(return_value=True)
    adapter.send_voice = AsyncMock(return_value=SimpleNamespace(success=True))
    assert (await adapter.play_tts("100", "/fake/audio.wav")).success
    adapter.play_in_voice_channel.assert_not_awaited()
    adapter.send_voice.assert_awaited_once_with(chat_id="300", audio_path="/fake/audio.wav")
