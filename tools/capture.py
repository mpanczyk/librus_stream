"""Jednorazowe pobranie stron z Librus Synergia do raw/capture/<konto>/ (NIE commitować).

Surowe strony zawierają dane osobowe; do testów służą zanonimizowane kopie w fixtures/.

Użycie: uv run python tools/capture.py [nr_konta] [--extra-only]
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import yaml
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://synergia.librus.pl"
DELAY = 2.0
MAX_DETAILS = 15

MAIN_PAGES = [
    "/rodzic/index",
    "/ogloszenia",
    "/wiadomosci",
    "/przegladaj_oceny/uczen",
    "/uwagi",
    "/terminarz",
    "/przegladaj_nb/uczen",
]
# Szczegóły ocen / frekwencji / terminarza są redundantne (dane są na listach),
# więc pobieramy tylko treść wiadomości.
DETAIL_PATTERNS = {
    "wiadomosc": re.compile(r"^/wiadomosci/\d+/\d+/\d+/f\d+$"),
}


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/")) or "root"


def login(page, acc):
    page.goto("https://portal.librus.pl/rodzina", wait_until="networkidle")
    try:
        page.click("button:has-text('Akceptuję')", timeout=5000)
    except Exception:
        pass
    page.click("button:has-text('LIBRUS Synergia')")
    page.wait_for_timeout(1000)
    page.click("a[href='/rodzina/synergia/loguj']")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    fr = next(f for f in page.frames if "api.librus.pl" in f.url)
    fr.wait_for_selector("#Login", state="visible", timeout=15000)
    fr.fill("#Login", acc["login"])
    fr.fill("#Pass", acc["password"])
    fr.click("#LoginBtn")
    page.wait_for_url("**synergia.librus.pl/**", timeout=30000)
    page.wait_for_load_state("networkidle")


def capture_account(p, idx, acc, root: Path, extra_only=False):
    out = root / f"account{idx}"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    json_seen = []
    hrefs = {}

    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="pl-PL", user_agent=UA)
    page = ctx.new_page()

    def on_response(resp):
        ct = resp.headers.get("content-type", "")
        if "json" in ct and "librus" in resp.url:
            try:
                body = resp.text()
            except Exception:
                return
            n = len(json_seen)
            fn = f"xhr{'x' if extra_only else ''}_{n:03d}.json"
            (out / fn).write_text(body)
            json_seen.append({"url": resp.url, "file": fn, "status": resp.status})

    page.on("response", on_response)
    login(page, acc)
    print(f"[{idx}] logged in: {page.url}", flush=True)

    def save_form(kind, name, js):
        time.sleep(DELAY)
        with page.expect_navigation(wait_until="networkidle"):
            page.evaluate(js)
        fn = f"{kind}__{name}.html"
        (out / fn).write_text(page.content())
        manifest.append({"path": name, "final_url": page.url, "file": fn, "status": 200, "kind": kind})
        print(f"[{idx}] form {name} -> {page.url} ({fn})", flush=True)

    def collect_hrefs(html):
        for h in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
            hrefs.setdefault(h, None)
        # onclick-y z otwieraniem szczegółów (np. terminarz)
        for h in re.findall(r"""['"](/(?:terminarz|przegladaj_oceny|przegladaj_nb|wiadomosci)/[^'"\s]+)['"]""", html):
            hrefs.setdefault(h, None)

    def followup(path):
        """Strony osiągalne tylko przez POST formularza (paginacja, kolejny miesiąc)."""
        if path == "/wiadomosci":
            if page.query_selector("#numer_strony105") and "Strona 1 z 1" not in page.content():
                save_form("main", "wiadomosci_page2",
                          "() => { const e = document.getElementById('numer_strony105'); e.value = 1; e.form.submit(); }")
                collect_hrefs(page.content())
        elif path == "/terminarz":
            save_form("main", "terminarz_next_month",
                      """() => { const m = document.querySelector('select[name=miesiac]');
                                 const r = document.querySelector('select[name=rok]');
                                 let mi = m.selectedIndex + 1;
                                 if (mi >= m.options.length) { mi = 0; r.selectedIndex += 1; }
                                 m.selectedIndex = mi; m.form.submit(); }""")

    def save(path, kind):
        time.sleep(DELAY)
        resp = page.goto(urljoin(BASE, path), wait_until="networkidle")
        fn = f"{kind}__{slug(path)}.html"
        (out / fn).write_text(page.content())
        manifest.append({"path": path, "final_url": page.url, "file": fn,
                         "status": resp.status if resp else None, "kind": kind})
        print(f"[{idx}] {path} -> {page.url} ({fn})", flush=True)
        return page.content()

    if extra_only:
        manifest_path = out / "manifest.json"
        old = json.loads(manifest_path.read_text())
        for path in ("/wiadomosci", "/terminarz"):
            save(path, "main")
            followup(path)
        old["pages"] += [m for m in manifest if m["path"] in ("wiadomosci_page2", "terminarz_next_month")]
        manifest_path.write_text(json.dumps(old, ensure_ascii=False, indent=2))
        ctx.close(); browser.close()
        return

    for path in MAIN_PAGES:
        collect_hrefs(save(path, "main"))
        followup(path)

    for kind, pat in DETAIL_PATTERNS.items():
        found = [h for h in hrefs if h and pat.match(h)]
        print(f"[{idx}] {kind}: {len(found)} detail links, saving {min(len(found), MAX_DETAILS)}", flush=True)
        for h in found[:MAX_DETAILS]:
            save(h, kind)

    (out / "manifest.json").write_text(json.dumps(
        {"pages": manifest, "xhr": json_seen, "all_hrefs": sorted(h for h in hrefs if h)},
        ensure_ascii=False, indent=2))
    ctx.close()
    browser.close()


def main():
    cfg = yaml.safe_load(open("config.yaml"))
    root = Path("raw/capture")
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None
    extra_only = "--extra-only" in sys.argv
    with sync_playwright() as p:
        for idx, acc in enumerate(cfg["accounts"]):
            if only is not None and idx != only:
                continue
            capture_account(p, idx, acc, root, extra_only)


if __name__ == "__main__":
    main()
