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
- **ETAP 2** — szkielet warstw, import-linter, ruff, mypy, CI. ← tutaj jesteśmy.

Następny: **ETAP 3** — `domain/`, `persistence/`, migracje i testy repozytoriów
na lokalnym PostgreSQL 17.

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
