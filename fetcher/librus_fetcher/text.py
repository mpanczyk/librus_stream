"""Konwersja fragmentów HTML z Librusa na tekst i Markdown."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

_BR = re.compile(r"<br\s*/?>", re.I)


def clean(s: str | None) -> str:
    """Normalizuje białe znaki (w tym &nbsp;) w jednej linii."""
    return " ".join((s or "").replace("\xa0", " ").split())


def _fragment_html(fragment: str | Tag) -> str:
    return fragment.decode_contents() if isinstance(fragment, Tag) else (fragment or "")


def html_to_text(fragment: str | Tag) -> str:
    """HTML -> tekst: <br> to nowa linia, pozostałe znaczniki usunięte, linki jako ich tekst."""
    html = _BR.sub("\n", _fragment_html(fragment))
    text = BeautifulSoup(html, "lxml").get_text()
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in text.split("\n")]
    return _squash_blank_lines("\n".join(lines))


def html_to_md(fragment: str | Tag) -> str:
    """HTML -> Markdown (linki, pogrubienia, listy itd.); <br> to twarde złamanie linii."""
    md = markdownify(_fragment_html(fragment), heading_style="ATX", bullets="-", strip=["img"])
    md = md.replace("\xa0", " ")
    lines = [line.rstrip() if not line.endswith("  ") else line.rstrip() + "  " for line in md.split("\n")]
    return _squash_blank_lines("\n".join(lines))


def _squash_blank_lines(s: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", s).strip()
