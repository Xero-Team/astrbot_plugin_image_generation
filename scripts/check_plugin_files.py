#!/usr/bin/env python3
"""Run dependency-free static checks for plugin-owned files."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "_conf_schema.json"
MANIFEST_PATH = ROOT / "pages" / "dashboard" / "assets.v1.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"
EXCLUDED_PARTS = {".agents", ".git", ".venv", "data", "dist", "tests"}
FORBIDDEN_ASTRBOT_PREFIXES = ("astrbot.core", "astrbot.dashboard")


def _fail(message: str) -> None:
    print(f"quality gate failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _check_strict_json() -> None:
    try:
        schema = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid _conf_schema.json: {exc}")
    if not isinstance(schema, dict):
        _fail("_conf_schema.json must contain an object")


def _check_pyproject() -> None:
    try:
        config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _fail(f"invalid pyproject.toml: {exc}")
    target_version = config.get("tool", {}).get("ruff", {}).get("target-version")
    if target_version != "py314":
        _fail("ruff target-version must be py314")
    requires_python = config.get("project", {}).get("requires-python")
    if requires_python != ">=3.14":
        _fail("project.requires-python must be >=3.14")


def _check_dashboard_assets() -> None:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid Dashboard assets manifest: {exc}")
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        _fail("Dashboard assets manifest must be version 1 with a files list")

    seen_paths: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            _fail("Dashboard asset entry must be an object")
        path_text = item.get("path")
        if not isinstance(path_text, str) or not path_text or path_text in seen_paths:
            _fail("Dashboard asset paths must be unique, non-empty strings")
        seen_paths.add(path_text)
        relative_path = Path(path_text)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            _fail(f"Dashboard asset path escapes plugin root: {path_text}")
        path = ROOT / relative_path
        if not path.is_file():
            _fail(f"Dashboard asset is missing: {path_text}")
        content = path.read_bytes()
        if item.get("size") != len(content):
            _fail(f"Dashboard asset size mismatch: {path_text}")
        if item.get("sha256") != hashlib.sha256(content).hexdigest():
            _fail(f"Dashboard asset digest mismatch: {path_text}")


def _check_public_sdk_boundary() -> None:
    for path in ROOT.rglob("*.py"):
        if EXCLUDED_PARTS.intersection(path.relative_to(ROOT).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            _fail(f"unable to parse {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "requests" or alias.name.startswith("requests."):
                        _fail(f"{path.relative_to(ROOT)} imports requests")
                    if alias.name.startswith(FORBIDDEN_ASTRBOT_PREFIXES):
                        _fail(
                            f"{path.relative_to(ROOT)} imports private AstrBot module {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(FORBIDDEN_ASTRBOT_PREFIXES):
                    _fail(
                        f"{path.relative_to(ROOT)} imports private AstrBot module {module}"
                    )


def main() -> None:
    _check_strict_json()
    _check_pyproject()
    _check_dashboard_assets()
    _check_public_sdk_boundary()
    print("plugin-owned file checks passed")


if __name__ == "__main__":
    main()
