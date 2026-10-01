from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from robinhood_mcp import __version__, cli


def test_cli_help_lists_all_command_groups() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "tools", "resources", "prompts", "serve"):
        assert command in result.stdout


def test_manual_login_uses_headless_flow(monkeypatch: object) -> None:
    completed: list[str] = []

    @dataclass
    class Flow:
        authorization_url: str = "https://example.test/authorize"

        async def complete(self, callback_url: str) -> None:
            completed.append(callback_url)

    class Client:
        def __init__(self, settings: object) -> None:
            del settings

        async def start_login(self, *, force: bool) -> Flow:
            assert force is False
            return Flow()

    monkeypatch.setattr(cli, "RobinhoodMCPClient", Client)
    result = CliRunner().invoke(
        cli.app,
        ["auth", "login", "--manual"],
        input="http://127.0.0.1:8765/oauth/callback?code=a&state=b\n",
    )
    assert result.exit_code == 0
    assert "https://example.test/authorize" in result.stdout
    assert completed == ["http://127.0.0.1:8765/oauth/callback?code=a&state=b"]


def test_cli_version_does_not_require_valid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_API_PORT", "invalid")
    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


@pytest.mark.parametrize("command", [["serve"], ["auth", "status"], ["tools", "list"]])
def test_cli_configuration_errors_are_readable(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setenv("ROBINHOOD_API_PORT", "invalid")
    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code == 1
    assert "Error [configuration_error]" in result.stderr
    assert "ROBINHOOD_API_PORT must be an integer" in result.stderr


def test_serve_rejects_public_binding_with_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBINHOOD_API_KEY", raising=False)
    result = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "Error [configuration_error]" in result.stderr
    assert "API_KEY is required" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    ["not-json", "[]", '{"value": NaN}', '{"value": Infinity}', '{"value": -Infinity}'],
)
def test_cli_rejects_invalid_json_before_connecting(arguments: str) -> None:
    result = CliRunner().invoke(cli.app, ["tools", "call", "echo", "--arguments", arguments])
    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_cli_missing_json_file_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    result = CliRunner().invoke(cli.app, ["tools", "call", "echo", "-a", f"@{path}"])
    assert result.exit_code == 2
    assert "readable UTF-8 file" in result.stderr


def test_cli_non_utf8_json_file_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff")
    result = CliRunner().invoke(cli.app, ["tools", "call", "echo", "-a", f"@{path}"])
    assert result.exit_code == 2
    assert "readable UTF-8 file" in result.stderr


def test_cli_json_file_preserves_utf8(tmp_path: Path) -> None:
    path = tmp_path / "arguments.json"
    path.write_text('{"name": "café"}', encoding="utf-8")
    assert cli._json_object(f"@{path}") == {"name": "café"}


def test_serve_uses_one_worker_and_omits_sensitive_access_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(application: object, **kwargs: object) -> None:
        assert application is not None
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", run)
    result = CliRunner().invoke(cli.app, ["serve", "--host", "127.0.0.1", "--port", "9001"])
    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["workers"] == 1
    assert captured["access_log"] is False
