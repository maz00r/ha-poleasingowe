"""Opcje add-onu z `/data/options.json` (SPEC.md §7.1).

Jedyne miejsce w projekcie, gdzie używamy pydantica — §5 mówi wprost, że to
jedyne miejsce, gdzie walidacja schematu się opłaca. Obiekty domenowe to
`@dataclass`, nie modele.

**Błędna konfiguracja = jasny komunikat i zatrzymanie**, nigdy działanie
z wartościami domyślnymi, które użytkownik uzna za swoje (§7.1).
"""

from __future__ import annotations

import json
import pathlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCIEZKA_OPCJI = pathlib.Path("/data/options.json")

# Twarda dolna granica interwalu (SPEC.md §11.2). Floor wylicza sie z reguly —
# polowa okna dogrywki serwisu — ale nizej niz 10 s nie schodzimy nigdy.
MINIMALNY_FLOOR_S = 10


class BladKonfiguracji(RuntimeError):
    """Opcje add-onu są niepoprawne. Add-on ma się zatrzymać, nie zgadywać."""


class Poswiadczenia(BaseModel):
    """Login i hasło do serwisu wymagającego zalogowania (SPEC.md §10.2).

    Poświadczenia pochodzą **wyłącznie** z opcji add-onu: nigdy z kodu, nigdy
    z obrazu, nigdy z logów, nigdy z `raw_json`.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    username: str
    password: str

    def __repr__(self) -> str:
        # Redakcja u zrodla: nawet przypadkowy repr w logu nie ma prawa
        # ujawnic hasla (SPEC.md §10.2).
        return f"Poswiadczenia(source={self.source!r}, username='***', password='***')"

    __str__ = __repr__


class OpcjeZrodla(BaseModel):
    """Per-źródłowy floor i limit tempa (SPEC.md §7.1, §11.2)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    enabled: bool = True
    rate_limit_per_minute: Annotated[int, Field(ge=1, le=600)] = 30
    floor_seconds: Annotated[int, Field(ge=MINIMALNY_FLOOR_S, le=86_400)] = 60


class Opcje(BaseModel):
    """Minimalny zestaw opcji z SPEC.md §7.1, z domyślnymi wg §0."""

    model_config = ConfigDict(extra="forbid")

    db_host: str = "db21ed7f-postgres-latest"
    db_port: Annotated[int, Field(ge=1, le=65_535)] = 5432
    db_name: str = "poleasingowe"
    db_user: str = "poleasingowe_app"
    db_password: str

    grafana_base_url: str = ""
    credentials: list[Poswiadczenia] = Field(default_factory=list)
    sources: list[OpcjeZrodla] = Field(default_factory=list)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    debug_dumps: bool = False
    notify_on_critical: bool = False

    @property
    def dsn(self) -> str:
        """Łańcuch połączenia. **Nie loguj go — zawiera hasło.**"""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def bezpieczny_opis(self) -> str:
        """Opis połączenia nadający się do logu (SPEC.md §10.2)."""
        return f"{self.db_user}@{self.db_host}:{self.db_port}/{self.db_name}"


def _komunikat(blad: ValidationError) -> str:
    linie = []
    for szczegol in blad.errors():
        sciezka = ".".join(str(x) for x in szczegol["loc"]) or "(korzeń)"
        linie.append(f"  {sciezka}: {szczegol['msg']}")
    return "\n".join(linie)


def wczytaj_opcje(sciezka: pathlib.Path = SCIEZKA_OPCJI) -> Opcje:
    """Wczytuje i waliduje opcje. Rzuca `BladKonfiguracji` z czytelnym opisem."""
    try:
        surowe = sciezka.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BladKonfiguracji(
            f"Brak pliku opcji {sciezka}. Add-on uruchamiany poza Home "
            "Assistant? Wskaż plik przez zmienną POLEASINGOWE_OPTIONS."
        ) from exc

    try:
        dane = json.loads(surowe)
    except json.JSONDecodeError as exc:
        raise BladKonfiguracji(f"{sciezka} nie jest poprawnym JSON-em: {exc}") from exc

    try:
        opcje = Opcje.model_validate(dane)
    except ValidationError as exc:
        raise BladKonfiguracji(
            f"Konfiguracja add-onu jest niepoprawna:\n{_komunikat(exc)}\n"
            "Popraw opcje w interfejsie Home Assistant i uruchom ponownie."
        ) from exc

    klucze = [z.key for z in opcje.sources]
    if len(klucze) != len(set(klucze)):
        raise BladKonfiguracji(
            f"Zduplikowane klucze źródeł w opcjach: {sorted(klucze)}"
        )
    return opcje
