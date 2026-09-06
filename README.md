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
  dla `grafana_ro`. Zostaje ręczne dodanie drugiego źródła danych w Grafanie —
  instrukcja w [`poleasingowe/DOCS.md`](poleasingowe/DOCS.md).
- **ETAP 5** — adapter EFL: parser, mapper, klient i rejestr, plus testy
  na fixtures. ← tutaj jesteśmy.

Następny: **ETAP 6** — opakowanie add-onu: Dockerfile, `config.yaml`, s6,
Ingress, AppArmor, i weryfikacja że wstaje na HAOS w budżecie z §1.1.

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

Warto włączyć hooki, żeby nie dowiadywać się o tym z CI:

```bash
uv pip install pre-commit && pre-commit install
```

## Architektura

Cztery warstwy, zależności wyłącznie do wewnątrz (`SPEC.md` §6):

```
poleasingowe/app/
  interfaces/       routery FastAPI, szablony, CLI  → wywołuje tylko application
  application/      use case'y, porty (Protocol), Unit of Work
  domain/           encje, value objects, reguły — ZERO zależności zewnętrznych
  infrastructure/   adaptery: sources/, persistence/, scheduler/, supervisor/
```

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

## Znane odstępstwa od SPEC.md

- `fixtures/` leży w korzeniu repo, a `SPEC.md` §7 umieszcza je w
  `poleasingowe/`. Przenosiny czekają na zakończenie pomiarów 0b, bo
  zaplanowane zadania odwołują się do obecnych ścieżek.
