from __future__ import annotations

from dataclasses import dataclass

from typer.testing import CliRunner

from robinhood_mcp import cli


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
