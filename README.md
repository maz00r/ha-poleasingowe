# Aukcje poleasingowe — add-on Home Assistant

Zbiera oferty z polskich serwisów aukcji samochodów poleasingowych, trzyma
pełną historię cen i udostępnia interfejs operacyjny przez Ingress.
**Aplikacja jest wyłącznie do odczytu** — nigdy nie licytuje, nie składa ofert
i nie zakłada kont.

To jest README dla dewelopera. Dokumentacja użytkownika add-onu trafi
do `poleasingowe/DOCS.md`.

| Dokument | Co zawiera |
|---|---|
| [`SPEC.md`](SPEC.md) | specyfikacja — wymagania, architektura, model danych |
| [`RECON.md`](RECON.md) | rekonesans źródeł: co który serwis udostępnia i jak |

## Stan prac

Kolejność etapów jest w `SPEC.md` §14. Zrobione:

- **ETAP 0** — rekonesans czterech źródeł, `RECON.md`, fixtures. Zaakceptowany.
  Pomiary czasowe (0b) w toku.
- **ETAP 2** — szkielet warstw, import-linter, ruff, mypy, CI.
- **ETAP 3** — `domain/`, `persistence/`, migracja `001_init.sql`, repozytoria
  i testy na lokalnym PostgreSQL 17.
- **ETAP 4** — migracja `002_reporting.sql`: cztery widoki i `GRANT SELECT`
  dla `grafana_ro`. Drugie źródło danych w Grafanie **dodane i sprawdzone na
  pustych widokach 2026-09-07** — instrukcja w
  [`poleasingowe/DOCS.md`](poleasingowe/DOCS.md). Etap zamknięty.
- **ETAP 5** — adapter EFL: parser, mapper, klient i rejestr, plus testy
  na fixtures.
- **ETAP 6** — opakowanie add-onu: `config.yaml`, Dockerfile, s6, Ingress,
  AppArmor, walidacja opcji. **Zainstalowany na HAOS 2026-09-07** (wersja
  0.2.1), łączy się do bazy jako `poleasingowe_app`.

  Pierwszy pomiar wobec budżetu z §1.1 — z panelu diagnostycznego, nie
  z oszacowania:

  | Pozycja | Budżet §1.1 | Zmierzone |
  |---|---|---|
  | RSS w spoczynku | < 170 MB | **60,9 MB** |
  | Rozmiar bazy | < 300 MB po roku | 7,7 MB (pusta, same tabele i widoki) |
  | Dryf zegara wobec Postgresa | — | 0,013 s |

  **To jest spoczynek bez dispatchera** — nic jeszcze nie odpytuje serwisów.
  Pozycja „RSS w szczycie (20 obserwowanych w dogrywce) < 250 MB" da się
  zmierzyć dopiero po etapie 9.
- **ETAP 7** — interfejs: lista z filtrami i paginacją keyset, szczegóły,
  watchlist, zapisane filtry, panel diagnostyczny, miniatury, pełna galeria
  i cache'owana wycena AI na podstawie danych auta oraz lokalnych porównań.
  ← tutaj jesteśmy.

- **ETAP 8** — mechanizm logowania: port `AuthenticatedSource`, trwały
  magazyn sesji (`/data/sessions/<key>.json`, 0600), reguły przejść
  `ANONYMOUS → OK → EXPIRED → LOCKED` w `domain/logowanie.py`, leniwe
  ponowne logowanie z jednym retry, wspólna redakcja logów i zrzutów,
  zrzuty diagnostyczne z rotacją. ← tutaj jesteśmy.

  **Logowania per serwis jeszcze nie ma** — powstaje razem z adapterem,
  który go potrzebuje. Realnie potrzebują go dwa źródła: autoprzetarg.pl
  (bez sesji nie ma liczby ofert) i poleasingowe.pl (pełna historia ofert).
  EFL i leasygroup czytają się w całości anonimowo, więc logowanie tam
  byłoby budowaniem czegoś, czego nie wolno używać — aplikacja nigdy nie
  licytuje.

- **ETAP 9** — dispatcher i `PollingPolicy`: czysta tabela progów
  w `domain/harmonogram.py`, floor wyliczany z okna dogrywki, kubełek tokenów
  z jitterem, bezpiecznik per źródło, pętla śpiąca do najbliższego terminu,
  tani odpyt po `content_hash`, `run_log` z RSS i rozmiarem bazy.
  Rejestracja źródeł przy starcie **zachowuje** stan uwierzytelnienia.
  ← tutaj jesteśmy.

- **ETAP 10** — adapter **poleasingowe.pl**: parser bloku Alpine, mapper,
  klient i rejestr, plus testy na fixtures i sprawdzenie na żywym serwisie.
  Heurystyka marki i modelu wyniesiona do `sources/marki.py`, wspólna dla
  adapterów. Dołożony adapter **autoprzetarg.pl** — czyta anonimowo wszystko
  poza liczbą ofert, bo tej serwis nie podaje bez sesji.
  ← zostaje leasygroup (po pomiarze 2026-09-10).

- **ETAP 11** — backup `pg_dump` własnej bazy raz na dobę do `/share`
  (siedem kopii, data w diagnostyce), trzy dashboardy Grafany jako JSON
  w repo wraz z testem wykonującym ich zapytania na prawdziwych widokach,
  eksport listy do CSV z tymi samymi filtrami, co widok.
  ← tutaj jesteśmy; **XLSX pominięty** — CSV z BOM-em i średnikiem otwiera
  się w Excelu bez pośrednika, a osobny format znaczyłby nową zależność.

- **Stabilizacja zbierania (0.27.0)** — wynik przemiatu jest rozróżniany na
  pełny, częściowy i nieudany; tylko dwa kolejne pełne przebiegi mogą oznaczyć
  aukcję jako znikniętą. Dispatcher najpierw łapie kończące się aukcje,
  następnie skanuje listy stronami, a backup idzie w tle z backoffem po błędzie.
  Pełny zestaw automatycznych testów przechodzi na lokalnym PostgreSQL 17.
  Przed uznaniem zachowania produkcyjnego za potwierdzone pozostaje
  siedmiodniowa obserwacja na HA, porównanie domknięć ze źródłami oraz pomiar
  RSS względem limitów 170 MB w spoczynku i 250 MB w szczycie.

Poza etapami doszła **faza domknięcia z §11.5** (drabinka prób po terminie,
`CONFIRMED` tylko z potwierdzenia serwisu), **wykrywanie zniknięcia z listy**,
**powiązania ponownych wystawień** tego samego auta oraz **przebieg
licytacji** na karcie, czyli historia ceny bieżącej. Wiersze zakładki
„Oferty" z EFL trafiają do tabeli `offer` (migracja `012`), ale **nie na
kartę**: nie da się ich ułożyć w chronologię licytacji i próba pokazania
ich jako takiej wprowadzała w błąd (RECON.md §3.5a). Dla **poleasingowe.pl**
jest odwrotnie — `lastOffers` to realny przebieg licytacji i karta go
pokazuje; bramką jest `bid_count_semantics = 'OFFERS'`, nie nazwa źródła.

Następny: **leasygroup** po pomiarze z 2026-09-10 (`tools/pomiar-leasygroup.sh`
uzbrojony w launchd). Logowanie z ETAPU 8 zostaje na razie odłożone —
mechanizm jest gotowy i przetestowany, ale żaden adapter jeszcze się nie
loguje.

Repozytorium add-onu: <https://github.com/maz00r/ha-poleasingowe> —
instrukcja instalacji w [`poleasingowe/DOCS.md`](poleasingowe/DOCS.md).

## Środowisko

Wymaga [`uv`](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
uv venv
uv pip install -e ".[dev]"
```

Docelowy runtime to **Python 3.12** — tyle ma obraz bazowy Home Assistant
(`ghcr.io/home-assistant/amd64-base-python:3.12-alpine`, `SPEC.md` §7).
Lokalnie można pracować na nowszym; `ruff` i `mypy` i tak sprawdzają zgodność
z 3.12, a CI chodzi na 3.12, więc rozjazd zostanie wyłapany.

## Lokalny PostgreSQL 17

Testy repozytoriów chodzą **wyłącznie** na lokalnej bazie i tworzą sobie
własne bazy tymczasowe. Nigdy na instancji w Home Assistant — tam mieszka
TeslaMate, którego danych nie da się odtworzyć (`SPEC.md` §2, §13).

```bash
brew install postgresql@17
brew services start postgresql@17
./scripts/setup-lokalny-postgres.sh
```

Klaster Homebrew powstaje z `--locale=en_US.UTF-8 -E UTF-8`, czyli **tak samo
jak serwer docelowy** (`SPEC.md` §0) — sortowanie zachowa się w testach jak na
produkcji. Skrypt tworzy role `poleasingowe_app` (bez superusera, z limitami
z §2 pkt 5) i `grafana_ro`.

Zatrzymanie, gdyby przeszkadzał: `brew services stop postgresql@17`.

**Czego lokalna baza NIE wyegzekwuje:** `poleasingowe_app` jest właścicielem
bazy, więc może instalować rozszerzenia *trusted* (m.in. `pg_trgm`) mimo braku
superusera. Zakaz z `SPEC.md` §8.3 pilnuje więc test
`poleasingowe/tests/unit/test_migracje.py`, a nie serwer.

## Bramki jakości

Wszystkie cztery chodzą tak samo lokalnie i w CI:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports
.venv/bin/pytest
```

### Piąta bramka, której nie sprawdzi żaden linter

**Każdy push zmieniający zachowanie dodatku musi podbić wersję.** Home
Assistant rozpoznaje dostępną aktualizację **wyłącznie po numerze wersji**
z `config.yaml` — bez podbicia poprawka leży w repo, a użytkownik nigdy się
o niej nie dowie. Kod wygląda wtedy na dostarczony i nie jest.

Numer stoi w trzech miejscach (`app/wersja.py`, `config.yaml`, `Dockerfile`);
ich zgodności pilnuje `tests/unit/test_pakowanie_addonu.py`, ale **samego
podbicia nie pilnuje nic**, bo żaden test nie wie, czy zmiana jest widoczna
dla użytkownika. Do tego wpis w `CHANGELOG.md` — do nowej sekcji, jeśli
poprzednia wersja została już wypchnięta.

Warto włączyć hooki, żeby nie dowiadywać się o tym z CI:

```bash
uv pip install pre-commit && pre-commit install
```

## Architektura

Cztery warstwy, zależności wyłącznie do wewnątrz (`SPEC.md` §6):

```
poleasingowe/app/
  interfaces/       routery FastAPI, szablony, CLI  → wywołuje tylko application
    web/            trasy, formularze, filtry szablonów
    templates/      Jinja2 — względny <base> zachowuje prefiks Ingressu
    static/         styl.css i htmx.min.js — bez CDN-ów (§12)
  application/      use case'y, porty (Protocol), Unit of Work, modele odczytu
  domain/           encje, value objects, reguły — ZERO zależności zewnętrznych
  infrastructure/   adaptery: sources/, persistence/, scheduler/, supervisor/
```

**Zapis i odczyt są rozdzielone.** Repozytoria z `application/ports.py` służą
use case'om i pracują na encjach; interfejs korzysta z osobnego portu
`Zapytania`, który zwraca modele odczytu z `application/read_models.py`.
Repozytorium próbujące obsłużyć obie strony kończy jako worek na zapytania
raportowe.

HTMX jest **wersjonowany w repo** (`interfaces/static/htmx.min.js`, 2.0.4),
nie ładowany z CDN-u — tego wymaga `SPEC.md` §12. Aktualizacja to podmiana
pliku, świadoma i widoczna w diffie.

Granic pilnuje **import-linter**, nie dobre chęci. Kontrakty są
w `pyproject.toml`; naruszenie wywala build. Że działają, sprawdzono celowym
naruszeniem — nie na wiarę.

Osobno `poleasingowe/tests/unit/test_architecture.py` pilnuje, że `domain/`
nie wciąga zależności zewnętrznej **pośrednio**, czego lista importów
w plikach nie pokazuje.

## Fixtures i dane osobowe

`fixtures/<serwis>/` zawiera realne zrzuty HTML, na których stoją testy
parserów. Selektory i nazwy pól mają pochodzić **wyłącznie stamtąd**
(`SPEC.md` §4).

**Zrzuty ze stron po zalogowaniu (`*-auth-*`) nigdy nie trafiają do repo** —
zawierają dane osobowe właściciela. Pilnują tego trzy niezależne warstwy:
`.gitignore`, hook pre-commit (`scripts/sprawdz-brak-auth.sh`) i osobny job
w CI, który sprawdza całe `git ls-files`.

Poza tym w danych z serwisów są dane osób trzecich, które **nie mogą** trafiać
do `raw_json` ani do zrzutów debug: pełny login zwycięzcy w poleasingowe.pl
(pole `winner`) i niepełne loginy uczestników w autoprzetarg.pl.

## Narzędzia

`tools/recon_0b.py` — pomiar domknięcia aukcji (jak długo cena jest widoczna
po wygaśnięciu, czy działa dogrywka, co odróżnia stronę zakończoną). Wyłącznie
odczyt, same GET-y. Obsługuje cztery źródła:

```bash
python3 tools/recon_0b.py --dry-run --source efl --url "<URL>"
```

## Znane luki

- **Faza domknięcia z §11.5 działa od 0.15.0, ale nie jest zmierzona na
  produkcji.** Po terminie obserwowanej aukcji dispatcher chodzi po
  drabince z `closing_ladder_seconds` (próby liczone od `ends_at`, nie od
  siebie nawzajem), zwolnionej z czekania na token. Gdy strona sama mówi
  „zakończona" — `auction_pending:false`, nagłówek `Zakończona` — cena
  zostaje zapisana jako `CONFIRMED`. Gdy drabinka się wyczerpie, aukcja
  kończy na `LAST_SEEN` razem z `last_price_lead_seconds`.
  Czego brakuje: siatka dla leasygroup pozostaje domyślna do czasu pomiaru,
  a `bid_history_ttl_seconds` nadal jest wypełnione i nieczytane — dla EFL
  przestało być potrzebne (lista ofert jest inline), dla pozostałych źródeł
  nie ma czego czytać.
- **Ponowne wystawienia bez VIN-u to tylko przypuszczenie.** Powiązanie
  po VIN jest pewne; bez niego opieramy się na zgodności marki, modelu,
  rocznika, silnika, koloru i przebiegu — i tak jest oznaczone w interfejsie.
  Auta z floty kupionej hurtem mogą się nie powiązać wcale (różny przebieg)
  i to jest bezpieczniejszy błąd niż sklejenie dwóch różnych sztuk.
- **Zniknięcie z listy wykrywane jest z jednym cyklem opóźnienia.** Aukcja
  musi wypaść z **dwóch** kolejnych przemiatów, żeby dostała `DISAPPEARED` —
  jeden przemiat potrafi urwać się w połowie (paginacja, timeout, WAF),
  a fałszywy alarm kasuje ofertę z widoku aktywnych. Aukcji po terminie ta
  reguła nie dotyczy: tam rządzi faza domknięcia.
- **Logowanie do serwisów** — mechanizm z etapu 8 stoi gotowy, ale żaden
  adapter go nie używa. Realnie zyskałby na tym tylko autoprzetarg
  (liczba i historia ofert).
- **Rodzaj pojazdu z poleasingowe.pl zgaduje się z nazwy.** Serwis nie podaje
  go osobnym polem na liście, więc pojazd bez nadwozia w tytule zostaje
  `NIEZNANY` i wypada z domyślnego filtru „osobowe". Widać go po przełączeniu
  na „wszystkie" albo „nierozpoznane" — to sygnał, żeby dopisać słowo do
  `sources/rodzaje.py`.
- **Zdjęcia z EFL nie zostały potwierdzone na żywo.** Poprawka (adres aukcji
  z bazy zamiast składanego) powstała na fixtures — serwis był w tym czasie
  nieosiągalny z maszyny deweloperskiej. Nieudane pobranie galerii loguje się
  teraz jako WARNING, więc gdyby przyczyna była inna, będzie ją widać.

## Znane odstępstwa od SPEC.md

- `fixtures/` leży w korzeniu repo, a `SPEC.md` §7 umieszcza je w
  `poleasingowe/`. Przenosiny czekają na zakończenie pomiarów 0b, bo
  zaplanowane zadania odwołują się do obecnych ścieżek.
