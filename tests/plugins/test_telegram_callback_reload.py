"""Real PTB callbacks survive a same-name forced plugin reload."""
import asyncio
import logging
import os
import textwrap
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.run_plugin_rewire import GatewayPluginRewireMixin
from hermes_cli.plugins import discover_plugins, get_plugin_manager
from tests.plugins.test_telegram_update_admission import connected, update


@pytest.mark.asyncio
async def test_same_plugin_force_reload_rewires_callback_after_unload(monkeypatch, caplog):
    home = Path(os.environ['HERMES_HOME'])
    directory = home / 'plugins' / 'callback_reload'
    directory.mkdir(parents=True)
    (directory / 'plugin.yaml').write_text("name: callback_reload\nversion: '0.1'\ndescription: offline callback reload repro\n")
    (directory / '__init__.py').write_text(textwrap.dedent('''
        import asyncio
        import logging
        import uuid
        from telegram.ext import CallbackQueryHandler, ApplicationHandlerStop

        def register(ctx):
            owned = []
            def factory(app, adapter):
                async def callback(update, context):
                    logging.getLogger('offline_callback_reload').info('callback_executed')
                    raise ApplicationHandlerStop
                handler = CallbackQueryHandler(callback)
                app.add_handler(handler, group=-100)
                owned.append((app, asyncio.get_running_loop(), handler))
            factory.__qualname__ += '_' + uuid.uuid4().hex
            ctx.register_telegram_handler(factory)
            def cleanup():
                def remove(app, handler):
                    app.remove_handler(handler, -100)
                for app, loop, handler in owned:
                    loop.call_soon_threadsafe(remove, app, handler)
            ctx.on_unload(cleanup)
    '''))
    (home / 'config.yaml').write_text('plugins:\n  enabled: [callback_reload]\n')
    discover_plugins(force=True)
    manager = get_plugin_manager()

    class Runner(GatewayPluginRewireMixin):
        pass

    async with connected(monkeypatch) as (adapter, app, _):
        runner = Runner()
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._subscribe_plugin_rewire(manager)
        with caplog.at_level(logging.INFO, logger='offline_callback_reload'):
            await app.process_update(update(app.bot, uid=9001, kind='callback'))
            assert sum(r.message == 'callback_executed' for r in caplog.records) == 1
            # The normal control-socket reload forces discovery on a worker thread.
            await asyncio.to_thread(discover_plugins, force=True)
            await asyncio.sleep(0)
            await app.process_update(update(app.bot, uid=9002, kind='callback'))
            assert sum(r.message == 'callback_executed' for r in caplog.records) == 2, (
                'Force reload removed native callbacks but failed to rewire the same plugin names')
        for unsubscribe in runner._plugin_rewire_unsubscribe.values():
            unsubscribe()
