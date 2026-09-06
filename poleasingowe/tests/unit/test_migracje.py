"""Bramka na migracje — to, czego PostgreSQL nam nie zabroni (SPEC.md §0, §2, §8.3).

Sprawdzone empirycznie na lokalnym PostgreSQL 17.11: rola `poleasingowe_app`
jest **właścicielem** bazy, a właściciel może instalować rozszerzenia oznaczone
jako „trusted" — w tym `pg_trgm`. Baza odmówi dopiero przy rozszerzeniu
nietrusted (`file_fdw` → „Must be superuser").

SPEC.md §8.3 uzasadnia zakaz rozszerzeń tym, że „rola aplikacji nie ma na to
uprawnień". Dla rozszerzeń trusted to nieprawda. Zakaz zostaje w mocy z drugiego
powodu podanego w tej samej sekcji — instalacja czegokolwiek w instancji
dzielonej z TeslaMate to zmiana, której właściciel chce uniknąć — ale
egzekwować musi go ten test, a nie serwer.
"""

from __future__ import annotations

import pathlib
import re

import pytest

MIGRACJE = pathlib.Path(__file__).resolve().parents[2] / "app" / "migrations"

# Operacje, ktorych migracja nie ma prawa zawierac. Klucz to wzorzec, wartosc
# to powod — trafia do komunikatu bledu, zeby nie trzeba bylo szukac w spec.
ZAKAZANE: dict[str, str] = {
    r"\bCREATE\s+EXTENSION\b": (
        "SPEC.md §8.3 — bez rozszerzeń. Baza tego NIE zablokuje dla rozszerzeń "
        "trusted (rola jest właścicielem bazy), więc pilnuje tego ten test"
    ),
    r"\bALTER\s+SYSTEM\b": (
        "SPEC.md §2 pkt 3 — nigdy nie zmieniamy parametrów globalnych"
    ),
    r"\bpg_terminate_backend\b": "SPEC.md §2 pkt 3 — nie zabijamy cudzych połączeń",
    r"\bVACUUM\s+FULL\b": "SPEC.md §2 pkt 3 — nie na instancji dzielonej z TeslaMate",
    r"\bCREATE\s+DATABASE\b": "SPEC.md §2 pkt 1 — add-on nigdy nie tworzy baz",
    r"\bDROP\s+DATABASE\b": "SPEC.md §2 pkt 1 — add-on nigdy nie usuwa baz",
    r"\bCREATE\s+SCHEMA\b": (
        "SPEC.md §0 — schematy app i reporting już istnieją; migracja 001 "
        "zaczyna od tabel"
    ),
    r"\bALTER\s+ROLE\b": (
        "SPEC.md §2 pkt 5 — limity roli są już ustawione i mają zostać"
    ),
    r"\btesla": "SPEC.md §2 pkt 2 — migracja nie ma prawa wspominać o bazie TeslaMate",
}


def _pliki_migracji() -> list[pathlib.Path]:
    return sorted(MIGRACJE.glob("*.sql")) if MIGRACJE.is_dir() else []


def _bez_komentarzy(sql: str) -> str:
    """Usuwa komentarze, żeby wzmianka w komentarzu nie wywalała testu."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


@pytest.mark.parametrize("wzorzec,powod", sorted(ZAKAZANE.items()))
def test_migracje_nie_wymagaja_uprawnien_nadrzednych(wzorzec: str, powod: str) -> None:
    naruszenia: list[str] = []
    for plik in _pliki_migracji():
        tresc = _bez_komentarzy(plik.read_text(encoding="utf-8"))
        if re.search(wzorzec, tresc, re.I):
            naruszenia.append(plik.name)
    assert not naruszenia, f"{naruszenia}: {powod}"


def test_migracje_sa_numerowane_bez_luk_i_duplikatow() -> None:
    """SPEC.md §5 — numerowane pliki .sql plus tabela `schema_migration`."""
    pliki = _pliki_migracji()
    if not pliki:
        pytest.skip("brak migracji — powstaną w kroku 3 z SPEC.md §14")
    numery = []
    for plik in pliki:
        m = re.match(r"^(\d{3})_", plik.name)
        assert m, f"{plik.name}: nazwa musi zaczynać się od trzycyfrowego numeru"
        numery.append(int(m.group(1)))
    assert numery == sorted(numery), f"migracje nie są posortowane: {numery}"
    assert len(numery) == len(set(numery)), f"zduplikowane numery migracji: {numery}"
    assert numery == list(range(1, len(numery) + 1)), f"luka w numeracji: {numery}"
