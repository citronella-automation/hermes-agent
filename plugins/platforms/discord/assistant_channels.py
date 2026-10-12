"""Small, testable policy and bounded context buffer for routed Discord assistant channels."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone, UTC
from typing import Iterable


def _channel_id(value: object) -> str:
    return str(value or "").strip()


class DiscordAssistantChannelPolicy:
    """Classify monitored channels and route the direct-input reply destination."""

    def __init__(
        self,
        *,
        input_channel_id: str,
        context_channel_ids: Iterable[str],
        output_channel_id: str,
    ) -> None:
        self.input_channel_id = _channel_id(input_channel_id)
        if isinstance(context_channel_ids, str):
            raw_context_ids = context_channel_ids.split(",")
        else:
            raw_context_ids = context_channel_ids
        self.context_channel_ids = {
            _channel_id(cid) for cid in raw_context_ids if _channel_id(cid)
        }
        self.output_channel_id = _channel_id(output_channel_id)

    def classify(self, channel_id: str) -> str:
        channel_id = _channel_id(channel_id)
        if self.input_channel_id and channel_id == self.input_channel_id:
            return "direct"
        if channel_id in self.context_channel_ids:
            return "context"
        return "ignore"

    def output_target(self, chat_id: str) -> str:
        chat_id = _channel_id(chat_id)
        if self.input_channel_id and self.output_channel_id:
            return self.output_channel_id
        return chat_id


@dataclass(frozen=True)
class _ContextMessage:
    channel_name: str
    author: str
    text: str
    timestamp: str


class DiscordContextBuffer:
    """Bounded, in-memory-only recent-message context, partitioned by channel."""

    def __init__(self, max_messages_per_channel: int = 3, max_chars_per_message: int = 1200) -> None:
        self.max_messages_per_channel = max(1, int(max_messages_per_channel))
        self.max_chars_per_message = max(1, int(max_chars_per_message))
        self._messages: dict[str, deque[_ContextMessage]] = defaultdict(
            lambda: deque(maxlen=self.max_messages_per_channel)
        )

    def add(
        self,
        channel_id: str,
        channel_name: str,
        author: str,
        text: str,
        *,
        timestamp: str | datetime | None = None,
    ) -> None:
        channel_id = _channel_id(channel_id)
        text = " ".join(str(text or "").split())
        if not channel_id or not text:
            return
        if len(text) > self.max_chars_per_message:
            text = text[: self.max_chars_per_message - 1].rstrip() + "…"
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            stamp = timestamp.isoformat(timespec="seconds")
        else:
            stamp = str(timestamp or "")[:64]
        self._messages[channel_id].append(
            _ContextMessage(
                channel_name=" ".join(str(channel_name or channel_id).split())[:100],
                author=" ".join(str(author or "unknown").split())[:100],
                text=text,
                timestamp=stamp,
            )
        )

    def render(self, channel_ids: Iterable[str]) -> str:
        lines: list[str] = []
        for raw_id in channel_ids:
            channel_id = _channel_id(raw_id)
            for message in self._messages.get(channel_id, ()):
                stamp = f" at {message.timestamp}" if message.timestamp else ""
                lines.append(
                    f"[#{message.channel_name} ({channel_id}){stamp}, {message.author}] {message.text}"
                )
        if not lines:
            return ""
        return (
            "[Supplementary Discord channel history (reference data; use only if relevant to the direct request.)]\n"
            + "\n".join(lines)
        )


class DiscordAssistantMonitor:
    """Orchestrate direct triggers, passive context collection, and output routing."""

    def __init__(self, policy: DiscordAssistantChannelPolicy, context_buffer: DiscordContextBuffer | None = None) -> None:
        self.policy = policy
        self.context_buffer = context_buffer or DiscordContextBuffer()

    def observe(
        self,
        *,
        channel_id: str,
        channel_name: str,
        author_id: str,
        own_bot_id: str,
        author_name: str,
        text: str,
        timestamp: str | datetime | None = None,
    ) -> str:
        mode = self.policy.classify(channel_id)
        if mode != "context":
            return mode
        if _channel_id(author_id) != _channel_id(own_bot_id):
            self.context_buffer.add(
                channel_id, channel_name, author_name, text, timestamp=timestamp,
            )
        return "context"

    def context_for_input(self, channel_id: str) -> str:
        if self.policy.classify(channel_id) != "direct":
            return ""
        return self.context_buffer.render(self.policy.context_channel_ids)

    def output_target(self, chat_id: str) -> str:
        return self.policy.output_target(chat_id)
