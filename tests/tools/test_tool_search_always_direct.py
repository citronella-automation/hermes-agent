"""``tools.tool_search.always_direct``: a named tool stays in the model-visible array.

Why this exists: every plugin-toolset tool defers by default (``is_deferrable_tool_name``
treats "not core, not a client surface" as deferrable), so a plugin tool the owner needs on
every turn — the source-bound ``task_add`` capture — is only reachable through the
tool_search/tool_describe/tool_call bridge and through the catalog listing embedded in
``tool_search``'s description. That listing is a per-assembly artifact that a long-lived
session pins as frozen bytes, so a tool registered after the pin was written is invisible to
that session. A tool an install relies on must be able to opt out of deferral.
"""

import json

import pytest


def _td(name, description):
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": {}}}}


def _register(name, toolset):
    from tools.registry import registry

    registry.register(
        name=name,
        handler=lambda args, **kwargs: json.dumps({"ok": True}),
        schema=_td(name, f"{name} capability")["function"],
        toolset=toolset,
    )


@pytest.fixture()
def task_tool():
    """A registered plugin tool exactly like the deployed ``task_add``."""
    from tools.registry import registry

    _register("task_add", "task-capture")
    yield "task_add"
    try:
        registry.deregister("task_add")
    except Exception:
        pass


class TestAlwaysDirect:
    def test_plugin_tool_defers_without_the_exemption(self, task_tool):
        """Baseline (the production defect): a plugin tool is behind the bridge."""
        from tools.tool_search import ToolSearchConfig, assemble_tool_defs

        assembled = assemble_tool_defs(
            [_td("terminal", "Run a command"), _td(task_tool, "Capture the owner's task")],
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on"}),
        )
        names = {td["function"]["name"] for td in assembled.tool_defs}

        assert assembled.activated
        assert "task_add" not in names
        assert "task_add" in assembled.tool_defs[0]["function"]["description"] + " ".join(
            td["function"].get("description", "") for td in assembled.tool_defs)

    def test_named_tool_stays_visible(self, task_tool):
        """The exemption keeps the named tool in ``tools[]`` (and out of the catalog)."""
        from tools.tool_search import ToolSearchConfig, assemble_tool_defs

        assembled = assemble_tool_defs(
            [_td("terminal", "Run a command"), _td(task_tool, "Capture the owner's task")],
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on", "always_direct": ["task_add"]}),
        )
        names = {td["function"]["name"] for td in assembled.tool_defs}

        assert "task_add" in names
        catalog_text = " ".join(td["function"].get("description", "")
                                for td in assembled.tool_defs
                                if td["function"]["name"].startswith("tool_"))
        assert "- task_add:" not in catalog_text

    def test_is_deferrable_accepts_the_exemption(self, task_tool):
        from tools.tool_search import is_deferrable_tool_name

        assert is_deferrable_tool_name("task_add", frozenset()) is True
        assert is_deferrable_tool_name(
            "task_add", frozenset(), frozenset({"task_add"})) is False
        # Bridge names stay direct regardless.
        assert is_deferrable_tool_name(
            "tool_search", frozenset(), frozenset({"tool_search"})) is False

    def test_config_parses_and_sanitises(self):
        from tools.tool_search import ToolSearchConfig

        cfg = ToolSearchConfig.from_raw({"always_direct": ["task_add", " ", 7, "food_x"]})
        assert cfg.always_direct == frozenset({"task_add", "food_x"})
        # Absent / malformed degrades to "no exemptions", never to a crash.
        assert ToolSearchConfig.from_raw({}).always_direct == frozenset()
        assert ToolSearchConfig.from_raw({"always_direct": "task_add"}).always_direct == frozenset()

    def test_defaults_carry_the_key(self):
        """The key is discoverable in DEFAULT_CONFIG (config discovery must not drift)."""
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        assert "always_direct" in DEFAULT_CONFIG["tools"]["tool_search"]
