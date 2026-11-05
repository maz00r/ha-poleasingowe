"""Opcje add-onu z `/data/options.json` (SPEC.md §7.1).

Jedyne miejsce w projekcie, gdzie używamy pydantica — §5 mówi wprost, że to
jedyne miejsce, gdzie walidacja schematu się opłaca. Obiekty domenowe to
`@dataclass`, nie modele.

**Błędna konfiguracja = jasny komunikat i zatrzymanie**, nigdy działanie
z wartościami domyślnymi, które użytkownik uzna za swoje (§7.1).
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCIEZKA_OPCJI = pathlib.Path("/data/options.json")
ZMIENNA_PLIKU_HASLA_BAZY = "POLEASINGOWE_DB_PASSWORD_FILE"
SCIEZKA_NADPISAN = pathlib.Path(
    os.environ.get("POLEASINGOWE_NADPISANIA", "/data/konfiguracja.json")
)
"""Zmiany z Diagnostyki. `options.json` bywa tylko do odczytu (Proxmox) albo
należy do Supervisora, więc to osobny plik z pierwszeństwem przed nim."""
SCIEZKA_PROFILI = pathlib.Path(
    os.environ.get("POLEASINGOWE_PROFILE", "/data/profile-ai.json")
)
POLA_NADPISYWANE = (
    "ai_provider",
    "ai_api_key",
    "ai_base_url",
    "ai_model",
    "openai_api_key",
    "wycena_auto",
    "wycena_auto_limit_dzienny",
    "wycena_pauza_minuty",
    "wycena_auto_rodzaje",
    "sources",
)
"""Tylko to wolno zmieniać z przeglądarki. Hasła do serwisów i bazy zostają
w opcjach add-onu."""

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


class ProfilAI(BaseModel):
    """Zapamiętany dostawca wyceny. Klucz jest na stałe sparowany z adresem."""

    model_config = ConfigDict(extra="forbid")

    nazwa: Annotated[str, Field(min_length=1, max_length=40)]
    ai_provider: Literal["openai", "anthropic", "zgodny_z_openai"]
    ai_api_key: Annotated[str, Field(min_length=1)]
    ai_base_url: str = ""
    ai_model: str = ""

    def __repr__(self) -> str:
        return f"ProfilAI(nazwa={self.nazwa!r}, ai_api_key='***')"

    __str__ = __repr__


class Opcje(BaseModel):
    """Minimalny zestaw opcji z SPEC.md §7.1, z domyślnymi wg §0."""

    model_config = ConfigDict(extra="forbid")

    db_host: str = "db21ed7f-postgres-latest"
    db_port: Annotated[int, Field(ge=1, le=65_535)] = 5432
    db_name: str = "poleasingowe"
    db_user: str = "poleasingowe_app"
    db_password: str

    grafana_base_url: str = ""

    # --- wycena AI (SPEC.md §12) ---------------------------------------
    ai_provider: Literal["openai", "anthropic", "zgodny_z_openai"] = "openai"
    """Kto liczy wycenę. `zgodny_z_openai` obejmuje OpenRouter, Groq,
    DeepSeek, Mistral, xAI i modele lokalne (Ollama, LM Studio) — wszystko,
    co wystawia `/chat/completions`."""
    ai_api_key: str = ""
    ai_base_url: str = ""
    """Wymagany TYLKO dla `zgodny_z_openai`; dla pozostałych pozwala wskazać
    proxy albo bramkę firmową."""
    ai_model: str = ""
    apify_token: str = ""
    """PRZESTARZAŁE (0.32.0): wycena z Otomoto nie używa już Apify. Pole
    zostaje przyjmowane, bo `extra="forbid"` odrzuciłby istniejące opcje."""
    """Pusty znaczy „domyślny dla dostawcy" (`ai_klienci.MODELE`)."""
    openai_api_key: str = ""
    """Poprzednia nazwa `ai_api_key`. Zostaje, żeby aktualizacja add-onu nie
    wyłączyła wyceny osobom, które mają ją już ustawioną — Supervisor nie
    przepisuje opcji sam, a `extra="forbid"` odrzuciłby nieznane pole."""
    wycena_auto: bool = True
    """Wyceniaj z Otomoto każdą aktywną aukcję w tle. Bez klucza AI nic to
    nie robi."""
    wycena_auto_limit_dzienny: Annotated[int, Field(ge=1, le=5_000)] = 300
    """Sufit prób na dobę — każda to płatne żądanie do modelu."""
    wycena_auto_rodzaje: Annotated[
        list[Literal["OSOBOWY", "DOSTAWCZY", "NIEZNANY"]], Field(min_length=1)
    ] = ["OSOBOWY", "DOSTAWCZY", "NIEZNANY"]
    """Co wycenia automat. Tylko rodzaje, które da się szukać na Otomoto;
    `NIEZNANY` obejmuje też motocykle i ciężarówki bez rozpoznanej kategorii.
    Przycisk na karcie aukcji działa niezależnie od tego wyboru."""
    wycena_pauza_minuty: Annotated[int, Field(ge=1, le=1_440)] = 5
    """Przerwa automatu po awarii dostawcy AI. Blokada Otomoto ma własną,
    stałą godzinę, bo to cudzy serwis."""
    credentials: list[Poswiadczenia] = Field(default_factory=list)
    sources: list[OpcjeZrodla] = Field(default_factory=list)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    debug_dumps: bool = False
    notify_on_critical: bool = False

    @property
    def dsn(self) -> str:
        """Łańcuch połączenia. **Nie loguj go — zawiera hasło.**"""
        uzytkownik = quote(self.db_user, safe="")
        haslo = quote(self.db_password, safe="")
        baza = quote(self.db_name, safe="")
        return (
            f"postgresql://{uzytkownik}:{haslo}"
            f"@{self.db_host}:{self.db_port}/{baza}"
        )

    @property
    def klucz_ai(self) -> str:
        """Klucz do API modelu, z nową nazwą opcji przed starą."""
        return self.ai_api_key or self.openai_api_key

    def bezpieczny_opis(self) -> str:
        """Opis połączenia nadający się do logu (SPEC.md §10.2)."""
        return f"{self.db_user}@{self.db_host}:{self.db_port}/{self.db_name}"


def _komunikat(blad: ValidationError) -> str:
    linie = []
    for szczegol in blad.errors():
        sciezka = ".".join(str(x) for x in szczegol["loc"]) or "(korzeń)"
        linie.append(f"  {sciezka}: {szczegol['msg']}")
    return "\n".join(linie)


def _nadpisania(sciezka: pathlib.Path) -> dict[str, Any]:
    """Zmiany z Diagnostyki albo pusty słownik, gdy ich nie ma."""
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise BladKonfiguracji(
            f"Nie można odczytać {sciezka}: {exc}. Usuń ten plik, a add-on "
            "wróci do opcji z options.json."
        ) from exc
    if not isinstance(dane, dict) or set(dane) - set(POLA_NADPISYWANE):
        raise BladKonfiguracji(
            f"{sciezka} ma niepoprawną zawartość. Usuń ten plik, a add-on "
            "wróci do opcji z options.json."
        )
    return dane


def zastosuj_zmiany(opcje: Opcje, zmiany: dict[str, Any]) -> Opcje:
    """Opcje po zmianach, sprawdzone tak samo jak przy starcie."""
    try:
        nowe = Opcje.model_validate({**opcje.model_dump(), **zmiany})
    except ValidationError as exc:
        raise BladKonfiguracji(_komunikat(exc)) from exc
    _sprawdz_spojnosc(nowe)
    return nowe


def zapisz_nadpisania(opcje: Opcje, sciezka: pathlib.Path) -> None:
    _zapisz_prywatnie(
        sciezka,
        json.dumps(opcje.model_dump(include=set(POLA_NADPISYWANE)), ensure_ascii=False),
    )


def wczytaj_profile(sciezka: pathlib.Path) -> list[ProfilAI]:
    """Profile posortowane po nazwie; brak pliku to brak profili."""
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8"))
        return sorted(
            (ProfilAI.model_validate(p) for p in dane), key=lambda p: p.nazwa.lower()
        )
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as exc:
        raise BladKonfiguracji(
            f"Nie można odczytać {sciezka}. Usuń ten plik i zapisz profile od nowa."
        ) from exc


def zapisz_profile(profile: list[ProfilAI], sciezka: pathlib.Path) -> None:
    _zapisz_prywatnie(
        sciezka,
        json.dumps([p.model_dump() for p in profile], ensure_ascii=False),
    )


def _zapisz_prywatnie(sciezka: pathlib.Path, tresc: str) -> None:
    """Atomowo i z prawami 0600, bo plik zawiera klucz API."""
    tymczasowy = sciezka.with_name(f"{sciezka.name}.tmp")
    deskryptor = os.open(tymczasowy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(deskryptor, "w", encoding="utf-8") as plik:
        plik.write(tresc)
        plik.flush()
        os.fsync(plik.fileno())
    tymczasowy.replace(sciezka)


def wczytaj_opcje(
    sciezka: pathlib.Path = SCIEZKA_OPCJI,
    nadpisania: pathlib.Path | None = None,
) -> Opcje:
    """Wczytuje i waliduje opcje. Rzuca `BladKonfiguracji` z czytelnym opisem.

    `nadpisania` to plik ze zmianami z Diagnostyki, ważniejszy od `sciezka`.
    """
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

    if nadpisania is not None:
        zmiany = _nadpisania(nadpisania)
        if zmiany:
            if not isinstance(dane, dict):
                raise BladKonfiguracji(
                    f"{sciezka} musi zawierać obiekt JSON, nie {type(dane).__name__}"
                )
            dane.update(zmiany)

    # Docker Compose przekazuje hasło bazy jako sekret montowany w pliku.
    # Home Assistant nie ustawia tej zmiennej i zachowuje dotychczasowe
    # zachowanie: hasło pochodzi z `/data/options.json`.
    plik_hasla = os.environ.get(ZMIENNA_PLIKU_HASLA_BAZY)
    if plik_hasla is not None:
        if not plik_hasla.strip():
            raise BladKonfiguracji(f"{ZMIENNA_PLIKU_HASLA_BAZY} wskazuje pustą ścieżkę")
        try:
            haslo = pathlib.Path(plik_hasla).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise BladKonfiguracji(
                f"Nie można odczytać sekretu bazy wskazanego przez "
                f"{ZMIENNA_PLIKU_HASLA_BAZY}: {exc}"
            ) from exc
        if not haslo:
            raise BladKonfiguracji(
                f"Sekret wskazany przez {ZMIENNA_PLIKU_HASLA_BAZY} jest pusty"
            )
        if not isinstance(dane, dict):
            raise BladKonfiguracji(
                f"{sciezka} musi zawierać obiekt JSON, nie {type(dane).__name__}"
            )
        dane["db_password"] = haslo

    try:
        opcje = Opcje.model_validate(dane)
    except ValidationError as exc:
        raise BladKonfiguracji(
            f"Konfiguracja add-onu jest niepoprawna:\n{_komunikat(exc)}\n"
            "Popraw opcje w interfejsie Home Assistant i uruchom ponownie."
        ) from exc

    _sprawdz_spojnosc(opcje)
    return opcje


def _sprawdz_spojnosc(opcje: Opcje) -> None:
    """Reguły obejmujące kilka pól naraz."""
    klucze = [z.key for z in opcje.sources]
    if len(klucze) != len(set(klucze)):
        raise BladKonfiguracji(
            f"Zduplikowane klucze źródeł w opcjach: {sorted(klucze)}"
        )
    # Wycena jest opcjonalna, ale WŁĄCZONA I ŹLE SKONFIGUROWANA to co innego
    # niż wyłączona: bez adresu poleciałaby do OpenAI, czyli nie tam, gdzie
    # użytkownik chciał (§7.1 — żadnych cichych wartości domyślnych).
    if opcje.klucz_ai and opcje.ai_provider == "zgodny_z_openai":
        if not opcje.ai_base_url.strip():
            raise BladKonfiguracji(
                "ai_provider = 'zgodny_z_openai' wymaga ai_base_url, np. "
                "https://openrouter.ai/api/v1 albo http://localhost:11434/v1"
            )
        if not opcje.ai_model.strip():
            raise BladKonfiguracji(
                "ai_provider = 'zgodny_z_openai' wymaga ai_model — nie ma tu "
                "modelu domyślnego, bo zależy od wybranego dostawcy"
            )
