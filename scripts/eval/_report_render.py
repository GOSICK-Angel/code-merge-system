"""Jinja2 rendering for the procedure.md §3.1 six-section eval report.

Encapsulates the template lookup + ``StrictUndefined`` policy so the
caller (``summarize.py``) only assembles the context dict and calls
:func:`render_report`. ``StrictUndefined`` makes a missing context key
fail loudly (``jinja2.UndefinedError``) rather than silently rendering
an empty field — Verifier T5-R2 explicitly guards this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Template,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "_templates"
TEMPLATE_NAME = "eval_report.md.j2"


def _build_env(templates_dir: Path = TEMPLATES_DIR) -> Environment:
    """Build a Jinja2 environment with strict-undefined and no autoescape.

    Autoescape stays off because the output is markdown (not HTML); we
    do not want ``<`` / ``>`` / ``&`` mangled in code blocks.
    """
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def _load_template(env: Environment, name: str = TEMPLATE_NAME) -> Template:
    return env.get_template(name)


def render_report(
    context: dict[str, Any],
    *,
    templates_dir: Path | None = None,
) -> str:
    """Render the eval report template against ``context`` and return markdown.

    Args:
        context: A dict carrying every key referenced in the template.
            Missing keys raise :class:`jinja2.UndefinedError`.
        templates_dir: Override the default templates location.
            Used by tests that want to validate against a sibling
            template.

    Returns:
        Rendered markdown string.
    """
    env = _build_env(templates_dir or TEMPLATES_DIR)
    template = _load_template(env)
    return template.render(**context)


__all__ = ["TEMPLATE_NAME", "TEMPLATES_DIR", "render_report"]
