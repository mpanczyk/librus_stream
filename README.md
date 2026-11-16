# librus_stream

Pobieranie i cache'owanie wpisów z dziennika **Librus Synergia** dla kilkorga dzieci.
Serwis `fetcher` cyklicznie loguje się na konto każdego dziecka (headless Chromium, Playwright),
przechodzi przez podstrony dziennika, parsuje je i zapisuje nowe wpisy w bazie SQLite.
Serwis `api` (FastAPI) będzie udostępniał zapisane dane na żądanie — na razie jest szkieletem.

Zbierane rodzaje wpisów:

| `kind`           | Źródło w dzienniku        | Wpis należy do          |
|------------------|---------------------------|-------------------------|
| `announcement`   | Ogłoszenia                | zawsze wspólny (`NULL`) |
| `message`        | Wiadomości (odebrane)     | dziecka lub wspólny     |
| `grade`          | Oceny                     | dziecka                 |
| `note`           | Uwagi                     | dziecka                 |
| `calendar_event` | Terminarz                 | dziecka lub wspólny     |
| `attendance`     | Frekwencja                | dziecka                 |

## Struktura

```
.
├── docker-compose.yml          # fetcher + api (prawdziwy Librus)
├── docker-compose.mock.yml     # nakładka: mock Librusa zamiast prawdziwego serwisu
├── config.example.yaml         # wzór konfiguracji (konta dzieci)
├── config.mock.yaml            # konfiguracja dla mocka (zmyślone konta)
├── pyproject.toml, uv.lock     # workspace uv (wspólny lockfile)
├── fetcher/                    # serwis pobierający (pakiet librus_fetcher)
│   └── librus_fetcher/
│       ├── __main__.py         # pętla cykli, --once
│       ├── browser.py          # sesja Playwright, logowanie
│       ├── scrape.py           # przejście przez podstrony jednego konta
│       ├── parsers.py          # HTML -> modele
│       ├── models.py           # modele pydantic wpisów
│       ├── db.py               # schemat SQLite, deduplikacja, wersjonowanie
│       └── text.py             # HTML -> tekst / Markdown
├── api/                        # serwis API (pakiet librus_api)
├── mock_librus/                # mock Librusa serwujący fixtures (pakiet mock_librus)
├── fixtures/synergia/          # zanonimizowane strony dziennika do testów i mocka
├── tests/                      # pytest
├── tools/                      # jednorazowe pobranie stron i ich anonimizacja
└── .github/workflows/tests.yml # CI: testy po każdym pushu
```

## Konfiguracja

Skopiuj wzór i uzupełnij dane logowania każdego dziecka:

```bash
cp config.example.yaml config.yaml
```

```yaml
accounts:
  - name: "Alfa"          # unikalna etykieta dziecka -> kolumna `student`
    login: "1234567"      # login do Librus Synergia
    password: "CHANGE_ME"
  # ... kolejne dzieci

fetcher:                  # opcjonalne, poniżej wartości domyślne
  interval_minutes: 60            # odstęp między cyklami
  request_delay_seconds: 2        # pauza między kolejnymi stronami
  calendar_months_ahead: 1        # ile kolejnych miesięcy terminarza sprawdzać
  headless: true
  base_url: "https://synergia.librus.pl"
  portal_url: "https://portal.librus.pl/rodzina"

database:
  path: "/data/librus.sqlite"     # ścieżka w kontenerze
```

`config.yaml` zawiera hasła — jest ignorowany przez git i nie trafia do obrazów Dockera
(`.dockerignore`).

## Uruchomienie (Docker Compose)

```bash
export UID GID=$(id -g)            # pliki bazy w ./data należą do użytkownika hosta
docker compose up -d --build
```

- `fetcher` wykonuje cykl od razu po starcie, a potem co `interval_minutes`.
  Baza trafia do `./data/librus.sqlite` (volumen w katalogu hosta).
- `api` nasłuchuje na `http://127.0.0.1:8001` (port hosta zmienisz przez `API_PORT`,
  np. `API_PORT=8005 docker compose up -d`). Na razie dostępne jest tylko `GET /health`.

Przydatne polecenia:

```bash
docker compose logs -f fetcher                                    # przebieg cykli
docker compose run --rm fetcher python -m librus_fetcher --once   # jeden cykl i koniec
docker compose restart fetcher                                    # po zmianie config.yaml
```

### Z mockiem zamiast prawdziwego Librusa

Mock serwuje strony z `fixtures/` i emuluje logowanie; trzy zmyślone konta
z `config.mock.yaml` wskazują na ten sam zestaw stron. Baza trafia do `./data-mock/`.

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml run --rm fetcher \
    python -m librus_fetcher --once
```

## Baza danych

Tabela `entries` — jeden wiersz na wersję wpisu:

| Kolumna             | Opis                                                                     |
|---------------------|--------------------------------------------------------------------------|
| `id`                | klucz                                                                    |
| `student`           | `accounts[].name` albo `NULL` dla wpisów wspólnych                       |
| `kind`              | rodzaj wpisu (tabela wyżej)                                              |
| `content`           | JSON wpisu (BLOB, UTF-8) — model z `models.py`                           |
| `fingerprint`       | SHA-256 całej treści; para (`kind`, `student`, `fingerprint`) unikalna   |
| `share_fingerprint` | SHA-256 treści bez pól zależnych od konta (np. identyfikatora Librusa)   |
| `created_at`        | moment zapisu, ISO 8601 (UTC)                                            |
| `superseded_by`     | `id` nowszej wersji tego samego elementu; `NULL` = wersja aktualna       |

Tabela pomocnicza `entry_sources` zapisuje, które dziecko widziało dany wpis i pod jakim
identyfikatorem Librusa (m.in. żeby nie otwierać ponownie znanych wiadomości).

Zasady zapisu (`db.persist`):

- **Bez duplikatów** — wpis o identycznej treści nie jest zapisywany ponownie.
- **Wpisy wspólne** — jeśli ta sama wiadomość lub wpis w terminarzu pojawia się u kilkorga dzieci,
  zostaje jeden wiersz z `student = NULL`. Oceny, uwagi i frekwencja nigdy nie są łączone.
- **Wersje** — gdy zmienia się treść elementu (np. poprawiona ocena, usprawiedliwiona nieobecność),
  powstaje nowy wiersz, a poprzedni dostaje `superseded_by`. Niejednoznaczne dopasowania są pomijane.

Wiadomości i opisy zapisywane są jako tekst (`content`) oraz Markdown (`content_md`,
`description_md`). Otwarcie treści wiadomości oznacza ją w Librusie jako przeczytaną.

Bieżące wpisy:

```sql
SELECT student, kind, json(CAST(content AS TEXT)), created_at
FROM entries WHERE superseded_by IS NULL ORDER BY created_at DESC;
```

## Rozwój

Zależnościami zarządza [uv](https://docs.astral.sh/uv/) (workspace: `fetcher`, `api`, `mock_librus`;
wersje przypięte w `pyproject.toml`, pełne drzewo w `uv.lock`, Python z `.python-version`).

```bash
uv sync                                                # środowisko deweloperskie (.venv)
uv run playwright install chromium-headless-shell      # przeglądarka dla testów
uv run pytest                                          # wszystkie testy
```

Testy działają wyłącznie na fixtures i mocku uruchamianym w wątku — nie łączą się z Librusem
i nie potrzebują `config.yaml`. Zmiana zależności: edytuj `pyproject.toml` danego serwisu
i uruchom `uv lock`.

CI (`.github/workflows/tests.yml`) uruchamia `uv sync --locked` i pełny zestaw testów
po każdym pushu w każdym branchu.

## Fixtures i anonimizacja

Strony w `fixtures/synergia/` zostały pobrane z dziennika **jeden raz** i zanonimizowane,
żeby testy nie obciążały infrastruktury Librusa i nie zawierały danych osobowych.

```bash
uv run python tools/capture.py 0          # pobranie stron konta nr 0 -> raw/capture/ (dane osobowe!)
uv run python tools/anonymize.py account0 # raw/ -> fixtures/ + automatyczna weryfikacja
```

`tools/anonymize.py` zastępuje m.in. imiona i nazwiska, treści, identyfikatory Librusa, adresy IP,
kod szkoły i oznaczenia klas, usuwa ślady aktywności konta (historia logowań, daty przeczytania,
liczniki), a wartości pochodne liczy HMAC-iem z sekretnym kluczem `raw/.anon_salt`. Po
zapisaniu sprawdza, czy w wynikach nie pozostały oryginalne wartości.
Katalog `raw/` i klucz są ignorowane przez git — nie udostępniaj ich.

Daty wpisów, godziny wiadomości, lista przedmiotów i kolory kategorii ocen pozostają oryginalne —
osoba z dostępem do dziennika szkoły mogłaby na ich podstawie rozpoznać szkołę i klasę.

## Uwagi

- Librus blokuje domyślny User-Agent przeglądarki headless; fetcher używa zwykłego UA Chrome
  (`fetcher.user_agent`).
- Pobierane są tylko wiadomości odebrane (bez archiwum i wysłanych). Wiersz „Zachowanie”
  w ocenach jest pomijany.
- Struktura stron Librusa może się zmienić — wtedy błąd sekcji trafia do logów, a pozostałe
  sekcje są zapisywane normalnie.

## Licencja

[MIT](LICENSE).
