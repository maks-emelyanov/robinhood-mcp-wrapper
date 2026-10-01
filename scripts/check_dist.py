"""Check release archives and smoke-test an installed wheel outside the checkout.

Run after ``uv build --no-sources`` with
``uv run --locked python scripts/check_dist.py``. Runtime dependencies come from
uv.lock; no credentials, live MCP connections, or publication are required.
"""

from __future__ import annotations

import argparse
import email
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_archives(directory: Path, name: str, version: str) -> Path:
    stem = f"{name.replace('-', '_')}-{version}"
    wheel = directory / f"{stem}-py3-none-any.whl"
    sdist = directory / f"{stem}.tar.gz"
    require(wheel.is_file() and sdist.is_file(), "Build the wheel and sdist first with uv build.")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        info = f"{stem}.dist-info"
        require("robinhood_mcp/py.typed" in names, "Wheel is missing the PEP 561 type marker.")
        require(f"{info}/licenses/LICENSE" in names, "Wheel is missing the MIT license file.")
        require(
            all(path.startswith(("robinhood_mcp/", f"{info}/")) for path in names),
            "Wheel contains files outside the package and its metadata.",
        )
        metadata = email.message_from_bytes(archive.read(f"{info}/METADATA"))
        require(metadata["Name"] == name, "Wheel project name does not match pyproject.toml.")
        require(metadata["Version"] == version, "Wheel version does not match pyproject.toml.")
        require(metadata["License-Expression"] == "MIT", "Wheel license metadata is missing.")
        require(metadata["Requires-Python"] == ">=3.14", "Wheel Python requirement is incorrect.")
        require(bool(metadata.get_all("Requires-Dist")), "Wheel has no runtime dependencies.")
        require(
            "robinhood-mcp = robinhood_mcp.cli:app"
            in archive.read(f"{info}/entry_points.txt").decode(),
            "Wheel is missing its CLI entry point.",
        )

    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name.removeprefix(f"{stem}/") for member in members}
        required = {
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "uv.lock",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CHANGELOG.md",
            "docs/architecture.md",
            "docs/operations.md",
            "docs/troubleshooting.md",
            "docs/maintaining.md",
            "examples/discover_tools.py",
            "src/robinhood_mcp/py.typed",
            "scripts/check_dist.py",
        }
        require(required <= names, f"Sdist is missing required files: {required - names}")
        for member in members:
            path = PurePosixPath(member.name)
            require(
                not path.is_absolute() and ".." not in path.parts,
                f"Unsafe archive path: {member.name}",
            )
            require(
                not any(part in {".git", ".venv", "__pycache__"} for part in path.parts)
                and not path.name.endswith((".pyc", ".pyo"))
                and not (path.name.startswith(".env") and path.name != ".env.example"),
                f"Local configuration or generated files found in sdist: {member.name}",
            )
    return wheel


def smoke_test(wheel: Path, project: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="robinhood-dist-") as temporary:
        directory = Path(temporary)
        smoke_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ROBINHOOD_") and key != "PYTHONPATH"
        }
        smoke_env["ROBINHOOD_CREDENTIALS_FILE"] = str(directory / "credentials.json")
        smoke_env["RUN_LIVE_ROBINHOOD"] = "0"
        requirements = directory / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=project,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        environment = directory / "venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        executables = environment / ("Scripts" if os.name == "nt" else "bin")
        python = executables / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--no-deps",
                "-r",
                str(requirements),
            ],
            cwd=directory,
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=directory,
            check=True,
        )
        code = """
import importlib.metadata
import importlib.resources
import pathlib
import sys
import robinhood_mcp
from robinhood_mcp.api import create_app

assert pathlib.Path(robinhood_mcp.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert robinhood_mcp.__version__ == importlib.metadata.version("robinhood-mcp-wrapper")
assert importlib.resources.files("robinhood_mcp").joinpath("py.typed").is_file()
assert any(route.path == "/healthz" for route in create_app().routes)
print("Installed wheel imports, version, type marker, and API factory passed.")
"""
        subprocess.run([str(python), "-I", "-c", code], cwd=directory, env=smoke_env, check=True)
        cli = executables / ("robinhood-mcp.exe" if os.name == "nt" else "robinhood-mcp")
        for command in ([str(cli)], [str(python), "-I", "-m", "robinhood_mcp"]):
            subprocess.run(
                [*command, "--help"],
                cwd=directory,
                env=smoke_env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            result = subprocess.run(
                [*command, "--version"],
                cwd=directory,
                env=smoke_env,
                check=True,
                capture_output=True,
                text=True,
            )
            require(result.stdout.strip() == version, "CLI version differs from package metadata.")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=project / "dist")
    arguments = parser.parse_args()
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    wheel = check_archives(arguments.dist_dir.resolve(), metadata["name"], metadata["version"])
    smoke_test(wheel, project, metadata["version"])
    print("Wheel and sdist checks passed; both CLI entry points work outside the checkout.")


if __name__ == "__main__":
    main()
