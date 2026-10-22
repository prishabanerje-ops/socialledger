"""OPTIONAL PDF output. Requires the ``pdf`` extra (``pipx install 'socialledger[pdf]'``),
which installs WeasyPrint; WeasyPrint needs system Pango libraries
(https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).

Without it, open ``report.html`` in a browser and use Print → Save as PDF.
"""

from __future__ import annotations

from pathlib import Path


class PdfUnavailable(Exception):
    pass


def write_pdf(html: str, target: Path) -> Path:
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise PdfUnavailable(
            "PDF output is optional and needs `socialledger[pdf]` (WeasyPrint). "
            "Alternatively open report.html in a browser and print to PDF."
        ) from exc
    HTML(string=html).write_pdf(str(target))  # pragma: no cover
    return target  # pragma: no cover
