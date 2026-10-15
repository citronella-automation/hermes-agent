"""Offline content-free callback diagnostics before queueing and admission."""
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from plugins.platforms.telegram.adapter import TelegramAdapter
from plugins.platforms.telegram import update_admission


def trace_callback_ingress(*args, **kwargs):
    function = getattr(update_admission, "trace_callback_ingress", None)
    assert callable(function), "Missing pre-admission callback diagnostic"
    return function(*args, **kwargs)


def raw_callback():
    return {"update_id": 41, "callback_query": {
        "id": "SECRET_CALLBACK", "data": "SECRET_PAYLOAD", "from": {"id": 12},
        "message": {"message_id": 100, "chat": {"id": 12, "type": "private"},
                    "from": {"id": 111, "is_bot": True}, "text": "SECRET_BODY"}}}


def test_wire_callback_logged_before_queue_without_private_content(caplog):
    adapter = SimpleNamespace(_updates_received_total=0, _polling_generation=6)
    with caplog.at_level(logging.INFO):
        TelegramAdapter._record_updates_received(adapter, [raw_callback()])
    assert adapter._updates_received_total == 1
    assert "stage=wire" in caplog.text
    assert "generation=6" in caplog.text
    assert "message_id=100" in caplog.text
    assert "message_bot_id=111" in caplog.text
    assert "SECRET" not in caplog.text


def test_trace_requires_callback_and_logs_only_trusted_numbers(caplog):
    with caplog.at_level(logging.INFO):
        trace_callback_ingress("admission", raw_callback(), generation=6, outcome="accepted")
        trace_callback_ingress("admission", {"update_id": 42, "message": {"text": "SECRET"}})
    assert len(caplog.records) == 1
    assert "stage=admission" in caplog.text
    assert "outcome=accepted" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_real_processor_and_admission_boundaries_report_duplicate(caplog, monkeypatch):
    from tests.plugins.test_telegram_update_admission import connected, update
    async with connected(monkeypatch) as (_adapter, app, _):
        incoming = update(app.bot, uid=500, kind="callback")
        with caplog.at_level(logging.INFO, logger=update_admission.__name__):
            await app.update_processor.process_update(incoming, app.process_update(incoming))
            await app.process_update(incoming)
        assert "stage=processor_enter" in caplog.text
        assert "stage=admission" in caplog.text
        assert "outcome=accepted" in caplog.text
        assert "outcome=seen" in caplog.text
        assert "ea:yes:expired" not in caplog.text

