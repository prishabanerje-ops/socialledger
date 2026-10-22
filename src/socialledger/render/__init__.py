"""Render a ``Report`` to Markdown and self-contained HTML (no external assets)."""

from __future__ import annotations

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from socialledger.report import Report
from socialledger.schema import PLATFORM_NAMES, Platform


def _num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    if digits == 0:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.2f}%"


def _env(autoescape: bool) -> Environment:
    env = Environment(
        loader=PackageLoader("socialledger", "render/templates"),
        # Markdown output is plain text by design; HTML output is always autoescaped.
        autoescape=select_autoescape(["html"]) if autoescape else False,  # noqa: S701
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["num"] = _num
    env.filters["pct"] = _pct
    env.filters["platform_name"] = lambda p: PLATFORM_NAMES.get(Platform(p), str(p))
    return env


def render_markdown(report: Report) -> str:
    return _env(autoescape=False).get_template("report.md.j2").render(r=report)


def render_html(report: Report) -> str:
    return _env(autoescape=True).get_template("report.html.j2").render(r=report)
