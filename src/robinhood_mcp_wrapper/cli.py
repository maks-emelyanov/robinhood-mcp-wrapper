"""Command-line interface for Robinhood MCP Wrapper."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, NoReturn

import typer
import uvicorn
from mcp_types import PromptReference, ResourceTemplateReference

from robinhood_mcp_wrapper import __version__
from robinhood_mcp_wrapper.api import create_app
from robinhood_mcp_wrapper.client import RobinhoodMCPClient
from robinhood_mcp_wrapper.config import Settings
from robinhood_mcp_wrapper.errors import RobinhoodMCPError
from robinhood_mcp_wrapper.serialization import to_jsonable

app = typer.Typer(name="robinhood-mcp-wrapper", no_args_is_help=True, help="Robinhood MCP Wrapper")
auth_app = typer.Typer(no_args_is_help=True, help="Manage Robinhood OAuth credentials")
tools_app = typer.Typer(no_args_is_help=True, help="Discover and call MCP tools")
resources_app = typer.Typer(no_args_is_help=True, help="List and read MCP resources")
prompts_app = typer.Typer(no_args_is_help=True, help="List and render MCP prompts")
app.add_typer(auth_app, name="auth")
app.add_typer(tools_app, name="tools")
app.add_typer(resources_app, name="resources")
app.add_typer(prompts_app, name="prompts")


def _settings(**overrides: object) -> Settings:
    try:
        return Settings.from_env(**overrides)
    except RobinhoodMCPError as exc:
        _exit_on_error(exc)


def _exit_on_error(exc: RobinhoodMCPError) -> NoReturn:
    typer.echo(f"Error [{exc.code}]: {exc.message}", err=True)
    if exc.details is not None:
        typer.echo(json.dumps(to_jsonable(exc.details), indent=2), err=True)
    raise typer.Exit(1) from exc


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed wrapper version and exit",
    ),
) -> None:
    """Robinhood MCP Wrapper."""


def _print(value: Any) -> None:
    typer.echo(json.dumps(to_jsonable(value), indent=2, sort_keys=True))


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
    except (OSError, UnicodeError) as exc:
        raise typer.BadParameter("JSON arguments file must be a readable UTF-8 file") from exc

    def reject_constant(constant: str) -> None:
        raise ValueError(f"{constant} is not a valid JSON number")

    try:
        parsed = json.loads(raw, parse_constant=reject_constant)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("JSON arguments must be an object")
    return parsed


def _run(awaitable: Any) -> Any:
    try:
        return asyncio.run(awaitable)
    except RobinhoodMCPError as exc:
        _exit_on_error(exc)


@auth_app.command("login")
def auth_login(
    manual: bool = typer.Option(False, "--manual", help="Paste the callback URL manually"),
    force: bool = typer.Option(False, "--force", help="Reauthorize existing credentials"),
) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        if manual:
            flow = await client.start_login(force=force)
            typer.echo("Open this URL in a browser:")
            typer.echo(flow.authorization_url)
            callback = await asyncio.to_thread(
                typer.prompt,
                "Paste the complete callback URL",
            )
            await flow.complete(callback)
        else:
            typer.echo("Opening Robinhood authorization in your browser...")
            await client.login_browser(force=force)
        typer.echo("Robinhood authorization completed.")

    _run(run())


@auth_app.command("status")
def auth_status() -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        _print(await client.auth_status())

    _run(run())


@auth_app.command("logout")
def auth_logout(
    forget_client: bool = typer.Option(
        False,
        "--forget-client",
        help="Also remove the dynamic client registration",
    ),
) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        if forget_client:
            await client.reset_credentials()
        else:
            await client.logout()
        typer.echo("Stored credentials removed.")

    _run(run())


@tools_app.command("list")
def tools_list(cursor: str | None = None, refresh: bool = False) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(await client.list_tools(cursor=cursor, refresh=refresh))

    _run(run())


@tools_app.command("call")
def tools_call(
    name: str,
    arguments: str = typer.Option("{}", "--arguments", "-a", help="JSON or @file"),
) -> None:
    payload = _json_object(arguments)

    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(await client.call_tool(name, payload))

    _run(run())


@resources_app.command("list")
def resources_list(
    cursor: str | None = None,
    refresh: bool = False,
    templates: bool = typer.Option(False, "--templates"),
) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            if templates:
                result = await client.list_resource_templates(cursor=cursor, refresh=refresh)
            else:
                result = await client.list_resources(cursor=cursor, refresh=refresh)
            _print(result)

    _run(run())


@resources_app.command("read")
def resources_read(uri: str, refresh: bool = False) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(await client.read_resource(uri, refresh=refresh))

    _run(run())


@prompts_app.command("list")
def prompts_list(cursor: str | None = None, refresh: bool = False) -> None:
    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(await client.list_prompts(cursor=cursor, refresh=refresh))

    _run(run())


@prompts_app.command("get")
def prompts_get(
    name: str,
    arguments: str = typer.Option("{}", "--arguments", "-a", help="JSON or @file"),
) -> None:
    payload = _json_object(arguments)
    string_payload = {key: str(value) for key, value in payload.items()}

    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(await client.get_prompt(name, string_payload))

    _run(run())


@prompts_app.command("complete")
def prompts_complete(
    ref_type: str = typer.Option(..., "--ref-type", help="prompt or resource"),
    ref_value: str = typer.Option(..., "--ref", help="Prompt name or resource template URI"),
    argument_name: str = typer.Option(..., "--argument-name"),
    value: str = typer.Option("", "--value"),
    context: str = typer.Option("{}", "--context", help="JSON or @file"),
) -> None:
    context_values = {key: str(item) for key, item in _json_object(context).items()}
    if ref_type == "prompt":
        ref = PromptReference(name=ref_value)
    elif ref_type == "resource":
        ref = ResourceTemplateReference(uri=ref_value)
    else:
        raise typer.BadParameter("--ref-type must be 'prompt' or 'resource'")

    async def run() -> None:
        client = RobinhoodMCPClient(_settings())
        async with client:
            _print(
                await client.complete(
                    ref,
                    {"name": argument_name, "value": value},
                    context_values,
                )
            )

    _run(run())


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, help="REST bind host"),
    port: int | None = typer.Option(None, help="REST bind port"),
) -> None:
    settings = _settings(host=host, port=port)
    try:
        settings.validate_server_binding()
    except RobinhoodMCPError as exc:
        _exit_on_error(exc)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level.lower(),
        # Uvicorn's access log includes OAuth callback codes in query strings.
        # The gateway middleware already logs request paths without queries.
        access_log=False,
    )
