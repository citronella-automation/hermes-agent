from plugins.platforms.discord.assistant_channels import (
    DiscordAssistantChannelPolicy,
    DiscordAssistantMonitor,
    DiscordContextBuffer,
)


def test_only_direct_input_channel_triggers_and_routes_to_output():
    policy = DiscordAssistantChannelPolicy(
        input_channel_id="input-1",
        context_channel_ids={"input-1", "context-1", "output-1"},
        output_channel_id="output-1",
    )

    assert policy.classify("input-1") == "direct"
    assert policy.classify("context-1") == "context"
    assert policy.classify("unmonitored") == "ignore"
    assert policy.output_target("input-1") == "output-1"
    assert policy.output_target("context-1") == "output-1"
    assert policy.output_target("arbitrary-tool-target") == "output-1"
    assert policy.output_target("dm") == "output-1"


def test_context_buffer_keeps_bounded_recent_messages_by_channel():
    buffer = DiscordContextBuffer(max_messages_per_channel=2, max_chars_per_message=20)
    buffer.add("c1", "#general", "A", "one", timestamp="t1")
    buffer.add("c1", "#general", "B", "two", timestamp="t2")
    buffer.add("c1", "#general", "C", "three is long enough to truncate", timestamp="t3")

    rendered = buffer.render({"c1"})

    assert "one" not in rendered
    assert "B" in rendered and "two" in rendered
    assert "C" in rendered and "three is long enoug" in rendered
    assert "#general" in rendered


def test_context_buffer_empty_is_empty():
    buffer = DiscordContextBuffer()
    assert buffer.render({"missing"}) == ""


def test_monitor_records_context_for_direct_channel_without_triggering_agent():
    policy = DiscordAssistantChannelPolicy(
        input_channel_id="input-1",
        context_channel_ids="context-1,output-1",
        output_channel_id="output-1",
    )
    monitor = DiscordAssistantMonitor(policy)

    mode = monitor.observe(
        channel_id="context-1", channel_name="#general", author_id="human-1",
        own_bot_id="bot-1", author_name="A", text="meeting moved", timestamp="t1",
    )

    assert mode == "context"
    assert monitor.context_for_input("input-1")
    assert monitor.context_for_input("context-1") == ""


def test_context_description_is_neutral_reference_data():
    buffer = DiscordContextBuffer()
    buffer.add("c1", "#general", "A", "meeting moved")

    rendered = buffer.render({"c1"})

    assert "Supplementary Discord channel history (reference data" in rendered

