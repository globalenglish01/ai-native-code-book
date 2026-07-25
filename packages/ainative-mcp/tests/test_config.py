from __future__ import annotations

import os

from ainative_mcp.config import build_mcp_config, build_safe_env, merge_mcp_configs


def test_build_safe_env_filters_to_whitelist(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "sk-should-not-leak")

    env = build_safe_env()
    assert "PATH" in env
    assert "SUPER_SECRET_API_KEY" not in env


def test_build_safe_env_extra_overrides_and_adds(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_safe_env(extra={"PATH": "/custom/path", "CUSTOM_KEY": "value"})
    assert env["PATH"] == "/custom/path"
    assert env["CUSTOM_KEY"] == "value"


def test_build_safe_env_respects_custom_whitelist():
    env = build_safe_env(safe_keys=frozenset({"NONEXISTENT_VAR_XYZ"}))
    assert env == {}


def test_build_mcp_config_omits_unset_optional_fields():
    config = build_mcp_config("my_server", "http", url="http://localhost:8000")
    assert config == {"my_server": {"transport": "http", "url": "http://localhost:8000"}}
    assert "command" not in config["my_server"]
    assert "env" not in config["my_server"]


def test_build_mcp_config_stdio_includes_command_and_env():
    config = build_mcp_config(
        "browser", "stdio", command="npx", args=["-y", "@playwright/mcp"], env={"FOO": "bar"},
    )
    entry = config["browser"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@playwright/mcp"]
    assert entry["env"] == {"FOO": "bar"}


def test_merge_mcp_configs_combines_multiple_servers():
    a = build_mcp_config("server_a", "http", url="http://a")
    b = build_mcp_config("server_b", "http", url="http://b")
    merged = merge_mcp_configs(a, b)
    assert set(merged.keys()) == {"server_a", "server_b"}


def test_merge_mcp_configs_later_overrides_earlier_on_name_collision():
    a = build_mcp_config("server", "http", url="http://old")
    b = build_mcp_config("server", "http", url="http://new")
    merged = merge_mcp_configs(a, b)
    assert merged["server"]["url"] == "http://new"
