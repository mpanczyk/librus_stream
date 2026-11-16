"""Mock serwisu Librus (portal + Synergia) serwujący zanonimizowane strony z fixtures.

Struktura fixtures (MOCK_FIXTURES_DIR, domyślnie ../fixtures/synergia):
  accounts.yaml            - konta: login, password, fixture (nazwa katalogu ze stronami)
  <fixture>/routes.json    - "GET /ścieżka" -> plik HTML, plus formularze POST
  <fixture>/*.html

Emulowany przepływ logowania (ten sam, którego używa fetcher wobec prawdziwego Librusa):
  GET /rodzina -> [Akceptuję] -> [LIBRUS Synergia] -> a[href=/rodzina/synergia/loguj]
  -> iframe /OAuth/Authorization z polami #Login, #Pass, #LoginBtn
  -> POST: ciasteczko DZIENNIKSID + przekierowanie okna nadrzędnego na /rodzic/index
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

FIXTURES_DIR = Path(os.environ.get(
    "MOCK_FIXTURES_DIR", Path(__file__).resolve().parents[2] / "fixtures" / "synergia"))
SESSION_COOKIE = "DZIENNIKSID"

app = FastAPI(title="Mock Librus Synergia")


# --------------------------------------------------------------------------- dane

@dataclass
class FixtureSet:
    root: Path
    routes: dict[str, str]
    calendar: dict[tuple[int, int], str] = field(default_factory=dict)  # (rok, miesiąc) -> plik

    def read(self, fn: str) -> str:
        return (self.root / fn).read_text(encoding="utf-8")


@lru_cache
def accounts() -> dict[str, dict]:
    data = yaml.safe_load((FIXTURES_DIR / "accounts.yaml").read_text(encoding="utf-8"))
    return {a["login"]: a for a in data["accounts"]}


@lru_cache
def fixture_set(name: str) -> FixtureSet:
    root = FIXTURES_DIR / name
    routes = json.loads((root / "routes.json").read_text(encoding="utf-8"))
    fs = FixtureSet(root=root, routes=routes)
    for key, fn in routes.items():
        if "terminarz" in key:
            ym = _selected_month(fs.read(fn))
            if ym:
                fs.calendar[ym] = fn
    return fs


def _selected_month(html: str) -> tuple[int, int] | None:
    soup = BeautifulSoup(html, "lxml")
    m = soup.select_one("select[name=miesiac] option[selected]")
    r = soup.select_one("select[name=rok] option[selected]")
    if m and r:
        return int(r["value"]), int(m["value"])
    return None


SESSIONS: dict[str, str] = {}  # token -> nazwa zestawu fixtures


def current_fixture(request: Request) -> FixtureSet | None:
    token = request.cookies.get(SESSION_COOKIE)
    name = SESSIONS.get(token or "")
    return fixture_set(name) if name else None


# --------------------------------------------------------------------------- portal + logowanie

PORTAL_HTML = """<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8"><title>Librus Rodzina (mock)</title></head>
<body>
<div id="consent"><button type="button" onclick="document.getElementById('consent').remove()">Akceptuję i przechodzę do serwisu</button></div>
<nav>
  <button type="button" onclick="document.getElementById('synergia-menu').style.display='block'">LIBRUS Synergia</button>
  <div id="synergia-menu" style="display:none"><a href="/rodzina/synergia/loguj">Zaloguj się</a></div>
</nav>
</body></html>"""

LOGIN_PAGE_HTML = """<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8"><title>Logowanie (mock)</title></head>
<body><iframe src="/OAuth/Authorization?client_id=46" width="400" height="400" title="Logowanie"></iframe></body></html>"""

LOGIN_FORM_HTML = """<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8"></head><body>
<form method="POST" action="/OAuth/Authorization?client_id=46">
  {error}
  <label for="Login">Login</label><input id="Login" name="Login" type="text">
  <label for="Pass">Hasło</label><input id="Pass" name="Pass" type="password">
  <button id="LoginBtn" type="submit">Zaloguj</button>
</form></body></html>"""


@app.get("/rodzina", response_class=HTMLResponse)
def portal() -> str:
    return PORTAL_HTML


@app.get("/rodzina/synergia/loguj", response_class=HTMLResponse)
def login_page() -> str:
    return LOGIN_PAGE_HTML


@app.get("/OAuth/Authorization", response_class=HTMLResponse)
def login_form() -> str:
    return LOGIN_FORM_HTML.format(error="")


@app.post("/OAuth/Authorization")
async def login_submit(request: Request) -> Response:
    form = await request.form()
    acc = accounts().get(str(form.get("Login", "")))
    if acc is None or not secrets.compare_digest(str(acc["password"]), str(form.get("Pass", ""))):
        return HTMLResponse(LOGIN_FORM_HTML.format(
            error='<div class="alert" role="alert">Nieprawidłowy login i/lub hasło.</div>'), status_code=200)
    token = secrets.token_hex(16)
    SESSIONS[token] = acc["fixture"]
    resp = HTMLResponse("<!DOCTYPE html><html><body><script>window.top.location.href = '/rodzic/index';"
                        "</script></body></html>")
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return resp


@app.get("/wyloguj")
def logout(request: Request) -> Response:
    SESSIONS.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    resp = RedirectResponse("/rodzina", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# --------------------------------------------------------------------------- strony dziennika

def _not_logged_in() -> Response:
    return RedirectResponse("/rodzina", status_code=302)


@app.post("/wiadomosci", response_class=HTMLResponse)
async def messages_post(request: Request) -> Response:
    fs = current_fixture(request)
    if fs is None:
        return _not_logged_in()
    form = await request.form()
    page = str(form.get("numer_strony105", "0"))
    fn = fs.routes.get(f"POST /wiadomosci numer_strony105={page}") if page != "0" else None
    return HTMLResponse(fs.read(fn or fs.routes["GET /wiadomosci"]))


@app.post("/terminarz", response_class=HTMLResponse)
async def calendar_post(request: Request) -> Response:
    fs = current_fixture(request)
    if fs is None:
        return _not_logged_in()
    form = await request.form()
    try:
        ym = (int(form["rok"]), int(form["miesiac"]))
    except (KeyError, ValueError):
        return HTMLResponse(fs.read(fs.routes["GET /terminarz"]))
    if ym in fs.calendar:
        return HTMLResponse(fs.read(fs.calendar[ym]))
    return HTMLResponse(_empty_calendar(fs, *ym))


def _empty_calendar(fs: FixtureSet, year: int, month: int) -> str:
    """Miesiąc bez fixtures: szablon bieżącego terminarza bez wpisów, z przestawionym miesiącem."""
    soup = BeautifulSoup(fs.read(fs.routes["GET /terminarz"]), "lxml")
    for day in soup.select("div.kalendarz-dzien table"):
        day.decompose()
    for name, value in (("miesiac", month), ("rok", year)):
        sel = soup.select_one(f"select[name={name}]")
        if sel is None:
            continue
        for opt in sel.select("option"):
            if opt.has_attr("selected"):
                del opt["selected"]
            if opt.get("value") == str(value):
                opt["selected"] = "selected"
    return "<!DOCTYPE html>\n" + str(soup.html)


@app.get("/{path:path}")
def synergia_page(path: str, request: Request) -> Response:
    if re.match(r"^(images|css|js|assets)/", path):
        return Response(status_code=404)
    fs = current_fixture(request)
    if fs is None:
        return _not_logged_in()
    fn = fs.routes.get(f"GET /{path}")
    if fn is None:
        m = re.fullmatch(r"wiadomosci/1/5/(\d+)/f0", path)
        html = _synthetic_message(fs, m.group(1)) if m else None
        if html is None:
            return HTMLResponse("<html><body>Nie znaleziono strony (mock)</body></html>", status_code=404)
        return HTMLResponse(html)
    return HTMLResponse(fs.read(fn))


def _synthetic_message(fs: FixtureSet, msg_id: str) -> str | None:
    """Treść wiadomości bez fixture: szablon innej wiadomości + nagłówki z listy odebranych."""
    row = None
    for key, fn in fs.routes.items():
        if key.startswith(("GET /wiadomosci", "POST /wiadomosci")) and "/1/5/" not in key:
            soup = BeautifulSoup(fs.read(fn), "lxml")
            cb = soup.select_one(f"input.wiadomoscLista[value='{msg_id}']")
            if cb is not None:
                row = cb.find_parent("tr")
                break
    template_fn = next((fn for key, fn in sorted(fs.routes.items()) if "/wiadomosci/1/5/" in key), None)
    if row is None or template_fn is None:
        return None
    tds = row.find_all("td", recursive=False)
    values = {"Nadawca": tds[2].get_text(" ", strip=True), "Temat": tds[3].get_text(" ", strip=True),
              "Wysłano": tds[4].get_text(" ", strip=True)}
    soup = BeautifulSoup(fs.read(template_fn), "lxml")
    for b in soup.select("td.medium.left > b"):
        label = b.get_text(strip=True)
        if label in values:
            b.find_parent("td").find_next_sibling("td").string = values[label]
    content = soup.select_one("div.container-message-content")
    content.clear()
    content.append(f"Treść wiadomości {msg_id} (wygenerowana przez mock).")
    for img in soup.select("img[onclick*='pobierz_zalacznik']"):
        table = img.find_parent("table")
        if table is not None:
            table.decompose()
    return "<!DOCTYPE html>\n" + str(soup.html)
