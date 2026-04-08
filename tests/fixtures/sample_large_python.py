"""Sample large Python file for testing AST chunking.

Contains imports, classes, functions, and module-level statements
to exercise the full chunking pipeline.
"""

import os
import sys
from pathlib import Path
from typing import Any, Optional

MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30


class Config:
    """Application configuration container."""

    def __init__(self, path: str, debug: bool = False) -> None:
        self.path = path
        self.debug = debug
        self._loaded = False

    def load(self) -> dict[str, Any]:
        if self._loaded:
            return {}
        self._loaded = True
        return {"path": self.path, "debug": self.debug}

    def validate(self) -> bool:
        if not self.path:
            return False
        return Path(self.path).exists()


def parse_args(argv: list[str]) -> dict[str, str]:
    """Parse command-line arguments into a dictionary."""
    result: dict[str, str] = {}
    for arg in argv:
        if "=" in arg:
            key, value = arg.split("=", 1)
            result[key.lstrip("-")] = value
    return result


def validate_input(data: dict[str, Any]) -> list[str]:
    """Validate input data and return list of errors."""
    errors: list[str] = []
    if "name" not in data:
        errors.append("Missing required field: name")
    if "email" not in data:
        errors.append("Missing required field: email")
    return errors


class Router:
    """HTTP request router."""

    def __init__(self) -> None:
        self._routes: dict[str, Any] = {}

    def get(self, path: str) -> Any:
        return self._routes.get(f"GET:{path}")

    def post(self, path: str) -> Any:
        return self._routes.get(f"POST:{path}")

    def delete(self, path: str) -> Any:
        return self._routes.get(f"DELETE:{path}")

    def _match_route(self, method: str, path: str) -> Optional[Any]:
        key = f"{method}:{path}"
        return self._routes.get(key)


def process_data(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Process a list of data items."""
    processed = []
    for item in items:
        if item.get("active"):
            processed.append({**item, "processed": True})
    return processed


def format_output(data: Any, style: str = "json") -> str:
    """Format output data in the specified style."""
    if style == "json":
        import json

        return json.dumps(data, indent=2)
    return str(data)


def main() -> None:
    """Entry point for the application."""
    config = Config(path="/etc/app.conf")
    if not config.validate():
        sys.exit(1)
    args = parse_args(sys.argv[1:])
    print(f"Running with args: {args}")
