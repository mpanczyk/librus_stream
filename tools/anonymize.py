"""Anonimizacja surowych stron z raw/capture/<konto>/ do fixtures/synergia/<konto>/.

- imiona i nazwiska (nauczyciele, nadawcy, rodzic, uczeń) -> spójnie mapowane, zmyślone,
- treści wolne (ogłoszenia, wiadomości, komentarze, uwagi, opisy w terminarzu,
  nazwy załączników) -> generyczny tekst o podobnej strukturze,
- identyfikatory Librusa -> zmyślone, spójnie mapowane,
- adresy IP (np. historia logowań "IP: x.x.x.x") -> adresy z zakresów dokumentacyjnych
  (IPv4: 192.0.2.0/24, RFC 5737; IPv6: 2001:db8::/32, RFC 3849), spójnie mapowane,
- ślady aktywności konta -> usuwane: historia logowań, daty "Przeczytano", stan
  przeczytania wiadomości (pogrubienia), liczniki nieprzeczytanych,
- znaczniki czasu z sekundami -> data i godzina bez zmian, minuty i sekundy losowe (deterministycznie),
- oznaczenia klas (np. "4c") -> zmyślone ("4a"); kod szkoły wykrywany z nagłówka "Klasa:",
- tokeny generowane przez serwer (np. linkButtonFn<32 hex>) -> losowe,
- wszystkie wartości pochodne (numery tytułów, nazwy załączników, minuty/sekundy, tokeny,
  dobór i długość tekstu zastępczego) liczone HMAC-iem z sekretnym kluczem (raw/.anon_salt,
  poza repozytorium) - bez klucza nie da się ich odtworzyć z prawdziwych danych i potwierdzić,
  z którego konta pochodzą fixtures; długość i liczba linii tekstu nie zależą od oryginału,
- usuwane: <script>, <style>, arkusze, tokeny requestkey.

Użycie: uv run python tools/anonymize.py [account0 ...]
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

RAW = Path("raw/capture")
OUT = Path("fixtures/synergia")
# Sekretny klucz HMAC - tworzony przy pierwszym uruchomieniu, trzymany obok surowych danych
# (katalog raw/ jest ignorowany przez git). Ten sam klucz => te same fixtures przy regeneracji.
SALT_FILE = RAW.parent / ".anon_salt"
_salt: bytes | None = None

FAKE_FIRST = ["Alfa", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Jota", "Kappa",
              "Lambda", "Mi", "Ni", "Ksi", "Omikron", "Pi", "Rho", "Sigma", "Tau", "Ypsilon",
              "Fi", "Chi", "Psi", "Omega"]
FAKE_LAST = ["Testowa", "Przykładowy", "Zmyślona", "Fikcyjny", "Wzorcowa", "Makieta", "Próbny",
             "Atrapa", "Szablon", "Wymyślona", "Modelowy", "Pozorna", "Umowny", "Roboczy",
             "Ćwiczebna", "Zastępczy", "Kontrolna", "Pilotażowy", "Doświadczalna", "Sondażowy",
             "Wstępna", "Orientacyjny", "Poglądowa", "Symboliczny", "Abstrakcyjna", "Hipotetyczny",
             "Teoretyczna", "Ilustracyjny", "Demonstracyjna", "Pokazowy"]

# Jedna pula zmyślonych słów dla imion i nazwisk - bez listy prawdziwych imion,
# która mogłaby zdradzać, kogo dotyczą dane.
FAKE_NAMES = FAKE_LAST + FAKE_FIRST

PARENT_FAKE = "Rodzic Testowy"
STUDENT_FAKE = ("Uczeń", "Testowy")  # (nazwisko, imię) w kolejności Librusa
SCHOOL_FAKE = "SP1"  # kod prawdziwej szkoły jest wykrywany z danych (Anonymizer.detect_school)

LOREM = [
    "Szanowni Państwo, przekazujemy informacje organizacyjne.",
    "Zajęcia odbędą się zgodnie z planem.",
    "Prosimy o zapoznanie się z treścią komunikatu.",
    "Szczegóły zostaną przekazane na najbliższym spotkaniu.",
    "W razie pytań prosimy o kontakt z wychowawcą.",
    "Uczniowie proszeni są o przygotowanie potrzebnych materiałów.",
    "Termin może ulec zmianie.",
    "Dziękujemy za współpracę.",
    "Zbiórka przed budynkiem szkoły.",
    "Obowiązuje strój galowy.",
    "Zakres materiału obejmuje ostatni dział.",
    "Zgłoszenia przyjmujemy do końca tygodnia.",
]

# Jedyny wyjątek od zastępowania nazwisk: lista wyboru miesiąca w terminarzu. To element
# interfejsu ("Maj" = maj), a nie dane - nazwisko np. "Nowak-Maj" i jego części są zastępowane.
UI_MONTH_SELECT = "select[name=miesiac]"

# słowa, które nigdy nie są nazwiskami (etykiety interfejsu)
STOP_TOKENS = {"Licz", "Temat", "Rodzaj", "Nadawca", "Nauczyciel", "Dodał", "Kategoria", "Data",
               "Waga", "Komentarz", "Opis", "Lekcja", "Wysłano", "Uczeń", "Klasa", "Pani", "Pan",
               "Brak", "Okres"}

# Kandydaci na adresy IP; ostateczna weryfikacja przez moduł ipaddress
# (dzięki temu godziny "12:34:56" nie są brane za IPv6).
IPV4_CANDIDATE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6_CANDIDATE = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
DOC_NETS = (ipaddress.ip_network("192.0.2.0/24"), ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"), ipaddress.ip_network("2001:db8::/32"))


def find_ips(s: str) -> list[tuple[int, int, ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    """Pozycje prawdziwych adresów IP (IPv4 i IPv6) w tekście."""
    out = []
    for pat in (IPV4_CANDIDATE, IPV6_CANDIDATE):
        for m in pat.finditer(s):
            try:
                out.append((m.start(), m.end(), ipaddress.ip_address(m.group(0))))
            except ValueError:
                continue
    return sorted(out, key=lambda x: x[0])


def is_doc_ip(ip) -> bool:
    return any(ip in net for net in DOC_NETS if net.version == ip.version)


# Liczby na pozycjach identyfikatorów Librusa - mapowane niezależnie od długości
# (ogólna reguła obejmuje tylko liczby od 5 cyfr, żeby nie ruszać lat, rozmiarów okien itp.).
ID_PREFIX = re.compile(
    r"(?:/szczegoly(?:_wolne)?/|/komentarz_oceny/\d+/|/wiadomosci/\d+/\d+/"
    r"|pobierz_zalacznik(?:\\?/\d+)?\\?/|usunWiadomosc\(|przedmioty_)$")
SERVER_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
DATETIME_SEC = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}):(\d{2}):(\d{2})")
# nagłówek strony ocen: "Klasa: <oznaczenie klasy> <kod szkoły>", np. "Klasa: 4c XY9"
CLASS_HEADER = re.compile(r"Klasa:\s*(\d[a-zA-Z]{1,2})\s+(\S+)")
LOGIN_HISTORY_MARK = "ostatnie udane logowania"

STRUCT_NAME_LABELS = ("Nauczyciel", "Dodał", "Nadawca", "Kto dodał")


def _key() -> bytes:
    global _salt
    if _salt is None:
        if not SALT_FILE.exists():
            SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
            SALT_FILE.write_bytes(secrets.token_bytes(32))
            SALT_FILE.chmod(0o600)
        _salt = SALT_FILE.read_bytes()
    return _salt


def h(s: str) -> int:
    """Deterministyczna, kluczowana (HMAC-SHA256) liczba pochodna - bez klucza nieodtwarzalna."""
    return int(hmac.new(_key(), s.encode(), hashlib.sha256).hexdigest(), 16)


class Anonymizer:
    def __init__(self):
        self.token_map: dict[str, str] = {}
        self.id_map: dict[str, str] = {}
        self._last_i = 0
        self._next_id = 100001
        self.ip_map: dict[str, str] = {}
        self.hex_map: dict[str, str] = {}
        self.class_map: dict[str, str] = {}
        self.raw_hex: set[str] = set()
        self.raw_datetimes: set[str] = set()
        self.school: str | None = None  # prawdziwy kod szkoły, wykryty z nagłówka "Klasa:"
        self.real_tokens: set[str] = set()

    # ---------- nazwiska ----------
    def add_person(self, text: str):
        text = re.sub(r"\(.*?\)|\[.*?\]", " ", text or "")
        for tok in re.findall(r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?", text):
            self.map_token(tok)
        # dopiski małymi literami w nazwie konta (np. "Testowa xyz Alfa") - nietypowe, więc identyfikujące
        for tok in re.findall(r"(?<!\S)[a-ząćęłńóśźż]{2,}(?!\S)", text):
            if tok not in self.token_map:
                n = sum(1 for k in self.token_map if k.islower())
                self.token_map[tok] = "dop" + ("" if n == 0 else str(n + 1))
                self.real_tokens.add(tok)

    def map_token(self, tok: str) -> str:
        if tok in STOP_TOKENS:
            return tok
        if tok in self.token_map:
            return self.token_map[tok]
        i = self._last_i
        fake = FAKE_NAMES[i % len(FAKE_NAMES)] + ("" if i < len(FAKE_NAMES) else str(i // len(FAKE_NAMES)))
        self._last_i += 1
        self.token_map[tok] = fake
        self.real_tokens.add(tok)
        # nazwisko dwuczłonowe: każda część też dostaje zmyślony odpowiednik,
        # żeby np. samo "Maj" z "Nowak-Maj" nie przeciekło
        if "-" in tok:
            for part in tok.split("-"):
                self.map_token(part)
        return fake

    def names_in(self, s: str) -> str:
        if not s or not self.token_map:
            return s
        pat = self._pattern()
        return pat.sub(lambda m: self.token_map[m.group(0)], s)

    def _pattern(self):
        keys = sorted(self.token_map, key=len, reverse=True)
        return re.compile(r"(?<![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])(" + "|".join(map(re.escape, keys)) + r")(?![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])")

    # ---------- identyfikatory ----------
    def fake_id(self, real: str) -> str:
        if set(real) == {"0"}:
            return real
        if real not in self.id_map:
            self.id_map[real] = str(self._next_id)
            self._next_id += 1
        return self.id_map[real]

    # ---------- adresy IP ----------
    def fake_ip(self, ip) -> str:
        key = str(ip)
        if key not in self.ip_map:
            n = sum(1 for v in self.ip_map.values() if (":" in v) == (ip.version == 6)) + 1
            if n > 250:
                raise RuntimeError("za dużo różnych adresów IP do zmapowania")
            self.ip_map[key] = f"192.0.2.{n}" if ip.version == 4 else f"2001:db8::{n:x}"
        return self.ip_map[key]

    def ips_in(self, s: str) -> str:
        if not s:
            return s
        out, last = [], 0
        for start, end, ip in find_ips(s):
            if start < last or ip.is_loopback or is_doc_ip(ip):
                continue
            out += [s[last:start], self.fake_ip(ip)]
            last = end
        return "".join(out) + s[last:]

    def ids_in(self, s: str) -> str:
        def sub(m: re.Match) -> str:
            num = m.group(0)
            if len(num) >= 5 or ID_PREFIX.search(s[:m.start()]):
                return self.fake_id(num)
            return num
        return re.sub(r"(?<![\d#])\d+(?!\d)", sub, s)

    # ---------- tokeny serwera ----------
    def hexes_in(self, s: str) -> str:
        def sub(m: re.Match) -> str:
            real = m.group(0)
            if real not in self.hex_map:
                self.hex_map[real] = f"{h('anon-hex:' + real):064x}"[:32]
            return self.hex_map[real]
        return SERVER_HEX.sub(sub, s)

    # ---------- znaczniki czasu ----------
    @staticmethod
    def times_in(s: str) -> str:
        def sub(m: re.Match) -> str:
            n = h(m.group(0))
            mm, ss = (n // 60) % 60, n % 60
            if (mm, ss) == (int(m.group(2)), int(m.group(3))):
                ss = (ss + 1) % 60
            return f"{m.group(1)}:{mm:02d}:{ss:02d}"
        return DATETIME_SEC.sub(sub, s)

    # ---------- szkoła i klasy ----------
    def detect_school(self, soup: BeautifulSoup) -> None:
        for p in soup.find_all("p"):
            m = CLASS_HEADER.search(" ".join(p.get_text(" ").replace("\xa0", " ").split()))
            if m:
                if self.school not in (None, m.group(2)):
                    raise RuntimeError("różne kody szkoły w danych jednego przebiegu")
                self.school = m.group(2)
                self._class_re = re.compile(r"\b([1-8])([a-zA-Z]{1,2})(?=\s+" + re.escape(self.school) + r"\b)")

    def school_in(self, s: str) -> str:
        return s.replace(self.school, SCHOOL_FAKE) if self.school else s

    def classes_in(self, s: str) -> str:
        def sub(m: re.Match) -> str:
            real = (m.group(1) + m.group(2)).lower()
            if real not in self.class_map:
                used = sum(1 for k in self.class_map if k[0] == real[0])
                self.class_map[real] = real[0] + "abcdefghijk"[used]
            fake = self.class_map[real]
            return fake.upper() if m.group(2).isupper() else fake
        return self._class_re.sub(sub, s) if self.school else s

    # ---------- tekst generyczny ----------
    @staticmethod
    def lorem(seed: str, approx_len: int | None = None) -> str:
        """1-3 zdania zastępcze; długość losowa (approx_len ignorowane - nie zdradzamy długości oryginału)."""
        n = h(seed)
        count = 1 + (n >> 8) % 3
        return " ".join(LOREM[(n + i * 7) % len(LOREM)] for i in range(count))

    @staticmethod
    def short(seed: str, prefix: str) -> str:
        return f"{prefix} {h(seed) % 1000:03d}"


def replace_rich_content(soup: BeautifulSoup, el: Tag, an: Anonymizer, seed: str):
    """Zastępuje treść elementu generycznym tekstem (2-5 linii, czasem link, czasem pusta linia).

    Struktura jest losowana z klucza, a nie przepisywana z oryginału, żeby liczba linii,
    długość tekstu i liczba linków nie były odciskiem palca prawdziwej wiadomości."""
    n = h(seed)
    el.clear()
    lines = 2 + n % 4
    for i in range(lines):
        if i:
            el.append(soup.new_tag("br"))
            if (n >> (i + 4)) % 4 == 0:  # pusta linia (akapit)
                el.append(soup.new_tag("br"))
        el.append(NavigableString(an.lorem(f"{seed}:{i}")))
    if (n >> 16) % 3 == 0:
        url = f"https://example.com/link/{(n >> 20) % 10000}"
        el.append(soup.new_tag("br"))
        na = soup.new_tag("a", href=url, target="_blank")
        na.string = url
        el.append(na)


def collect_people(soup: BeautifulSoup, an: Anonymizer):
    # tabele "etykieta -> wartość" (ogłoszenia, szczegóły wiadomości)
    for th in soup.find_all("th"):
        if th.get_text(strip=True) in STRUCT_NAME_LABELS:
            cell = th.find_next_sibling("td")
            if cell is not None:
                an.add_person(cell.get_text(" ", strip=True))
    for b in soup.select("td.medium.left > b"):
        if b.get_text(strip=True) in STRUCT_NAME_LABELS:
            cell = b.find_parent("td").find_next_sibling("td")
            if cell is not None:
                an.add_person(cell.get_text(" ", strip=True))
    # tooltipy (oceny, frekwencja, terminarz)
    for el in soup.find_all(attrs={"title": True}):
        for part in re.split(r"<br\s*/?>", el["title"]):
            k, _, v = part.partition(":")
            if k.strip() in STRUCT_NAME_LABELS:
                an.add_person(re.sub(r"<[^>]+>", "", v))
    # lista wiadomości - kolumna nadawcy
    for a in soup.select("table.decorated a[href^='/wiadomosci/1/']"):
        td = a.find_parent("td")
        if td is not None and td.find_previous_sibling("td") is not None and \
                td.find_previous_sibling("td").find("input") is None and td.find_next_sibling("td") is not None \
                and td.find_next_sibling("td").find("a", href=a["href"]) is not None:
            an.add_person(a.get_text(" ", strip=True))
    # tabela uwag: kolumna "Kto dodał"
    for table in soup.select("table.decorated"):
        heads = [td.get_text(strip=True) for td in table.select("thead td")]
        if "Kto dodał" in heads:
            idx = heads.index("Kto dodał")
            for tr in table.select("tbody tr"):
                tds = tr.find_all("td", recursive=False)
                if len(tds) > idx:
                    an.add_person(tds[idx].get_text(" ", strip=True))
        # tabela ocen rozwinięta: Nauczyciel / Dodał
        for table2 in table.select("table.stretch"):
            heads2 = [td.get_text(strip=True) for td in table2.select("thead td")]
            for label in ("Nauczyciel", "Dodał"):
                if label in heads2:
                    i = heads2.index(label)
                    for tr in table2.select("tbody tr.detail-grades"):
                        tds = tr.find_all("td", recursive=False)
                        if len(tds) > i:
                            an.add_person(tds[i].get_text(" ", strip=True))
    # terminarz: zastępstwa i odwołane zajęcia
    for td in soup.select("div.kalendarz-dzien td"):
        txt = td.get_text(" ", strip=True)
        for m in re.finditer(r"(?:Zastępstwo z|Odwołane zajęcia)\s+(.+?)\s+na lekcji", txt):
            an.add_person(m.group(1))
    # nagłówek "<b>Uczeń:</b> Nazwisko Imię<br/><b>Klasa:</b> ..."
    for p in soup.find_all("p"):
        m = re.match(r"\s*Uczeń:\s*(.+?)\s*Klasa:", p.get_text(" ", strip=True))
        if m:
            real = m.group(1).split()
            if len(real) >= 2:
                an.token_map[real[0]] = STUDENT_FAKE[0]
                an.token_map[real[1]] = STUDENT_FAKE[1]
                an.real_tokens.update(real[:2])


def clean(soup: BeautifulSoup):
    for t in soup.find_all(["script", "style", "noscript", "iframe", "link", "meta"]):
        t.decompose()
    for c in soup.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    for img in soup.find_all("img"):
        if (img.get("src") or "").startswith("data:"):
            img["src"] = "/images/placeholder.png"
    for el in soup.find_all(style=True):
        if "url(data:" in el["style"]:
            del el["style"]
    for i in soup.select("input[name=requestkey]"):
        i["value"] = "FAKE_REQUEST_KEY"
    scrub_activity(soup)
    # sekcja użytkownika (zalogowany jako ...)
    us = soup.select_one("#user-section")
    if us is not None:
        for b in us.find_all("b"):
            if b.get_text(strip=True):
                b.string = "1" if b.find_parent(class_="luckyNumber") else PARENT_FAKE


def scrub_activity(soup: BeautifulSoup):
    """Usuwa ślady aktywności konta, które da się zestawić z logami serwera Librusa."""
    # historia logowań (tooltip i alt obrazka w nagłówku)
    for el in soup.find_all(True):
        for attr in ("title", "alt"):
            if LOGIN_HISTORY_MARK in (el.get(attr) or ""):
                el[attr] = f"<b>{LOGIN_HISTORY_MARK}:</b><br />(usunięte z fixtures)"
    # "Przeczytano: <data>" w szczegółach wiadomości
    for b in soup.select("td b"):
        if b.get_text(strip=True) == "Przeczytano":
            tr = b.find_parent("tr")
            if tr is not None:
                tr.decompose()
    # stan przeczytania na liście wiadomości: wszystkie jako przeczytane
    for cb in soup.select("input.wiadomoscLista"):
        for td in cb.find_parent("tr").find_all("td", recursive=False):
            if "font-weight" in (td.get("style") or ""):
                del td["style"]
    # liczniki: "odebrane (N)" w drzewie folderów, licznik nieprzeczytanych w menu
    for a in soup.find_all("a"):
        t = a.get_text()
        if re.search(r"\(\d+\)", t) and re.match(r"\s*(odebrane|uwagi|wysłane|kosz)\b", t):
            a.string = re.sub(r"\s*\(\d+\)", "", t).strip()
    for el in soup.select(".button.counter"):
        el.decompose()


def anonymize_free_text(soup: BeautifulSoup, an: Anonymizer, fname: str):
    # ogłoszenia: tytuł + treść
    for table in soup.select("table.decorated.big"):
        ths = {th.get_text(strip=True): th for th in table.select("th")}
        if "Treść" in ths and "Data publikacji" in ths:
            head = table.select_one("thead td")
            if head is not None:
                head.string = an.short(head.get_text() + fname, "Ogłoszenie testowe")
            td = ths["Treść"].find_next_sibling("td")
            replace_rich_content(soup, td, an, "ogl:" + td.get_text()[:200])
    # lista wiadomości: temat
    for tr in soup.select("table.decorated tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 5 and tr.select_one("input.wiadomoscLista"):
            a = tds[3].find("a")
            if a is not None:
                a.string = an.short(a.get_text(), "Wiadomość testowa")
    # szczegóły wiadomości
    for b in soup.select("td.medium.left b"):
        if b.get_text(strip=True) == "Temat":
            td = b.find_parent("td").find_next_sibling("td")
            td.string = an.short(td.get_text(), "Wiadomość testowa")
    for div in soup.select("div.container-message-content"):
        replace_rich_content(soup, div, an, "msg:" + div.get_text()[:200])
    for td in soup.find_all("td"):
        img = td.find("img", src=re.compile("filetype_icons"), recursive=False)
        if img is not None:
            ext = re.search(r"filetype_icons/(\w+)\.png", img["src"])
            for s in list(td.find_all(string=True)):
                if s.strip():
                    s.replace_with(f" zalacznik_{h(s) % 1000:03d}.{ext.group(1) if ext else 'pdf'} ")
    # uwagi: treść (pierwsza kolumna)
    for table in soup.select("table.decorated"):
        heads = [td.get_text(strip=True) for td in table.select("thead td")]
        if heads[:1] == ["Uwaga"]:
            for tr in table.select("tbody tr"):
                tds = tr.find_all("td", recursive=False)
                if tds:
                    tds[0].string = an.lorem("uwaga:" + tds[0].get_text(), min(len(tds[0].get_text()), 100))
    # tooltipy: Komentarz / Opis
    for el in soup.find_all(attrs={"title": True}):
        parts = re.split(r"(<br\s*/?>)", el["title"])
        out = []
        for part in parts:
            m = re.match(r"(\s*(?:Komentarz|Opis)\s*:)(.*)", part, re.S)
            if m and m.group(2).strip():
                out.append(m.group(1) + " " + an.lorem(part, min(len(m.group(2)), 60)))
            else:
                out.append(part)
        # opis w terminarzu może mieć wiele linii - usuń linie poza strukturą "Etykieta: wartość"
        joined = "".join(out)
        if "Opis:" in joined:
            segs = re.split(r"<br\s*/?>", joined)
            keep, in_desc = [], False
            for s in segs:
                if s.strip().startswith("Opis:"):
                    in_desc = True
                    keep.append(s)
                    continue
                if in_desc and not re.match(r"\s*(Data dodania|Nauczyciel)\s*:", s):
                    continue
                in_desc = False
                keep.append(s)
            joined = "<br />".join(keep)
        el["title"] = joined
    # rozwinięta tabela ocen nie ma komentarza w treści; nic do zrobienia


def apply_names_and_ids(soup: BeautifulSoup, an: Anonymizer):
    ui_strings = {id(x) for sel in soup.select(UI_MONTH_SELECT) for x in sel.find_all(string=True)}
    for s in list(soup.find_all(string=True)):
        if id(s) in ui_strings:
            continue
        new = an.ips_in(an.school_in(an.times_in(an.classes_in(an.names_in(str(s))))))
        if new != s:
            s.replace_with(new)
    for el in soup.find_all(True):
        for attr, val in list(el.attrs.items()):
            if isinstance(val, list):
                continue
            new = an.ips_in(an.school_in(an.times_in(an.classes_in(an.names_in(val)))))
            new = an.hexes_in(new)
            if attr in ("href", "onclick", "value", "id") or attr.startswith("data-"):
                new = an.ids_in(new)
            if new != val:
                el[attr] = new


KEEP_KINDS = {"main", "wiadomosc"}


def process_account(name: str, an: Anonymizer):
    src = RAW / name
    dst = OUT / name
    dst.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((src / "manifest.json").read_text())
    pages = [p for p in manifest["pages"] if p["kind"] in KEEP_KINDS]
    # dedupe po ścieżce (ostatnia wersja wygrywa)
    by_path = {p["path"]: p for p in pages}

    soups = {}
    for p in by_path.values():
        raw_html = (src / p["file"]).read_text()
        an.raw_hex |= set(SERVER_HEX.findall(raw_html))
        an.raw_datetimes |= {m.group(0) for m in DATETIME_SEC.finditer(raw_html)}
        soup = BeautifulSoup(raw_html, "lxml")
        clean(soup)
        collect_people(soup, an)
        soups[p["path"]] = soup
    for soup in soups.values():
        an.detect_school(soup)
    if an.school is None:
        raise RuntimeError("nie wykryto kodu szkoły (nagłówek 'Klasa:' na stronie ocen) - "
                           "bez niego anonimizacja byłaby niepełna")
    # zawsze rodzic z nagłówka strony
    for soup in soups.values():
        us = soup.select_one("#user-section")
        if us is not None:
            t = us.get_text(" ", strip=True)
            m = re.search(r"zalogowany jako:\s*(.+?)\s*\(", t)
            if m:
                an.add_person(m.group(1))

    routes = {}
    for path, soup in soups.items():
        anonymize_free_text(soup, an, path)
        apply_names_and_ids(soup, an)
        fake_path = an.ids_in(path) if path.startswith("/") else path
        fn = re.sub(r"[^A-Za-z0-9]+", "_", fake_path.strip("/")) + ".html"
        (dst / fn).write_text("<!DOCTYPE html>\n" + str(soup.html))
        if path == "wiadomosci_page2":
            routes["POST /wiadomosci numer_strony105=1"] = fn
        elif path == "terminarz_next_month":
            routes["POST /terminarz next_month"] = fn
        else:
            routes[f"GET {fake_path}"] = fn
    (dst / "routes.json").write_text(json.dumps(dict(sorted(routes.items())), ensure_ascii=False, indent=2))
    return dst


def verify(dst: Path, an: Anonymizer, extra_forbidden: list[str]):
    forbidden = {t for t in an.real_tokens if len(t) >= 3} | set(extra_forbidden) | {an.school}
    forbidden |= {k for k in an.id_map if len(k) >= 5}  # oryginalne identyfikatory (krótkie - niżej)
    fake_ids = set(an.id_map.values())
    problems = []
    for f in dst.glob("*.html"):
        soup = BeautifulSoup(f.read_text(), "lxml")
        for sel in soup.select(UI_MONTH_SELECT):  # nazwy miesięcy w interfejsie
            sel.decompose()
        text = str(soup)
        for tok in forbidden:
            if re.search(r"(?<![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])" + re.escape(tok) + r"(?![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])", text):
                problems.append((f.name, tok[0] + "*" * (len(tok) - 1)))
        # identyfikatory na pozycjach ID muszą pochodzić z mapowania
        for m in re.finditer(r"\d+", text):
            if ID_PREFIX.search(text[max(0, m.start() - 60):m.start()]) and set(m.group(0)) != {"0"} \
                    and m.group(0) not in fake_ids:
                problems.append((f.name, f"ID: {m.group(0)[0]}***"))
        for tok in SERVER_HEX.findall(text):
            if tok in an.raw_hex:
                problems.append((f.name, f"token serwera: {tok[:4]}***"))
        for m in DATETIME_SEC.finditer(text):
            if m.group(0) in an.raw_datetimes:
                problems.append((f.name, f"czas z sekundami: {m.group(0)[:10]} **:**:**"))
        for real in an.class_map:
            if re.search(r"(?<![\w-])" + re.escape(real) + r"(?![\w-])", text, re.I):
                problems.append((f.name, f"klasa: {real[0]}*"))
        if "Przeczytano" in text:
            problems.append((f.name, "data przeczytania"))
        if 'style="font-weight: bold;"' in text and "wiadomoscLista" in text:
            problems.append((f.name, "stan przeczytania wiadomości"))
        for _, _, ip in find_ips(text):
            if not (ip.is_loopback or is_doc_ip(ip)):
                problems.append((f.name, f"IP: {str(ip).split('.')[0]}.***"))
        for m in re.finditer(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{3}[ -]?\d{3}[ -]?\d{3}\b", text):
            problems.append((f.name, "PII-like: " + m.group(0)[:2] + "***"))
    return problems


def main():
    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    extra = []
    for acc in cfg["accounts"]:
        extra += [acc["login"], acc["name"]]
    names = sys.argv[1:] or sorted(p.name for p in RAW.iterdir() if p.is_dir())
    an = Anonymizer()
    for n in names:
        dst = process_account(n, an)
        problems = verify(dst, an, extra)
        print(f"{n}: {len(list(dst.glob('*.html')))} pages -> {dst}; problems: {len(problems)}")
        for p in problems[:50]:
            print("   ", p)


if __name__ == "__main__":
    main()
