"""Testy kontraktowe adapterów (SPEC.md §13).

„Jeden sparametryzowany zestaw sprawdzający, że każdy adapter spełnia
`AuctionSource`. Nowy adapter dostaje testy za darmo."

Testy są **offline**: sprawdzają kształt adaptera, nie ruch sieciowy.
"""

from __future__ import annotations

import datetime as dt
import inspect

import pytest

from app.application.ports import AuctionSource, SurowaOferta
from app.domain.entities import Auction
from app.infrastructure.sources import registry

KLUCZE = sorted(registry.REJESTR)


def test_rejestr_nie_jest_pusty() -> None:
    assert KLUCZE, "rejestr bez adapterów oznacza, że kontrakt niczego nie sprawdza"


@pytest.mark.parametrize("klucz", KLUCZE)
def test_adapter_ma_klucz_zgodny_z_rejestrem(klucz: str) -> None:
    adapter = registry.utworz(klucz)
    assert adapter.key == klucz


@pytest.mark.parametrize("klucz", KLUCZE)
@pytest.mark.parametrize("metoda", ["przemiec_liste", "pobierz_szczegoly", "na_aukcje"])
def test_adapter_ma_metody_portu(klucz: str, metoda: str) -> None:
    adapter = registry.utworz(klucz)
    assert callable(
        getattr(adapter, metoda, None)
    ), f"{klucz}: brak metody {metoda} wymaganej przez AuctionSource"


@pytest.mark.parametrize("klucz", KLUCZE)
def test_metody_sieciowe_sa_asynchroniczne(klucz: str) -> None:
    """SPEC.md §6.3 zabrania mieszania sync i async w jednej ścieżce wywołań."""
    adapter = registry.utworz(klucz)
    for metoda in ("przemiec_liste", "pobierz_szczegoly"):
        assert inspect.iscoroutinefunction(
            getattr(adapter, metoda)
        ), f"{klucz}.{metoda} musi być async"


@pytest.mark.parametrize("klucz", KLUCZE)
def test_na_aukcje_jest_czyste_i_zwraca_encje(klucz: str) -> None:
    """Mapowanie nie ma prawa dotykać sieci — musi działać bez żadnego I/O."""
    adapter = registry.utworz(klucz)
    assert not inspect.iscoroutinefunction(adapter.na_aukcje)

    teraz = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)
    surowa = SurowaOferta(external_id="1", url="https://x", pola={"tytul": "Audi A4"})
    wynik = adapter.na_aukcje(surowa, 1, teraz)
    assert isinstance(wynik, Auction)
    assert wynik.external_id == "1"
    assert wynik.source_id == 1
    # SPEC.md §8.2 — czas do bazy zawsze swiadomy strefy.
    assert wynik.first_seen_at.tzinfo is not None


@pytest.mark.parametrize("klucz", KLUCZE)
def test_adapter_spelnia_protokol_statycznie(klucz: str) -> None:
    """Sprawdzenie na poziomie typów, nie tylko obecności atrybutów."""
    adapter: AuctionSource = registry.utworz(klucz)
    assert adapter is not None


def test_nieznane_zrodlo_konczy_sie_czytelnym_bledem() -> None:
    with pytest.raises(KeyError, match="nieznane źródło"):
        registry.utworz("nie-ma-takiego")


def _lancuch(wyjatek: BaseException) -> list[BaseException]:
    """Rozwija przyczyny i grupy wyjątków — httpx pakuje błędy w ExceptionGroup."""
    stos, zebrane = [wyjatek], []
    while stos:
        e = stos.pop()
        zebrane.append(e)
        if isinstance(e, BaseExceptionGroup):
            stos.extend(e.exceptions)
        if e.__cause__ is not None:
            stos.append(e.__cause__)
        if e.__context__ is not None and e.__context__ is not e.__cause__:
            stos.append(e.__context__)
    return zebrane


async def test_blokada_sieci_w_testach_jednostkowych_dziala() -> None:
    """Sprawdza samą blokadę — strażnik, który nie łapie, jest gorszy niż żaden."""
    import httpx

    from tests.unit.conftest import SiecWTescieJednostkowym

    try:
        async with httpx.AsyncClient(timeout=2.0) as klient:
            await klient.get("https://aukcje.efl.com.pl/")
    except BaseException as exc:
        assert any(
            isinstance(e, SiecWTescieJednostkowym) for e in _lancuch(exc)
        ), f"połączenie zablokowane, ale nie przez naszego strażnika: {exc!r}"
    else:
        pytest.fail("żądanie sieciowe przeszło — blokada nie działa")


@pytest.mark.parametrize("klucz", KLUCZE)
def test_adapter_oglasza_tylko_kodowania_ktore_umie_rozpakowac(klucz: str) -> None:
    """Najkosztowniejszy błąd tego projektu, gdyby został niezauważony.

    Adaptery miały wpisany na sztywno `Accept-Encoding: gzip, br`, ale httpx
    bez pakietu `brotli` nie ma dekodera dla `br`. autoprzetarg.pl wybierał
    brotli i dostawaliśmy bajty nie do odczytania — parser widział pustą
    listę i **nie zgłaszał błędu**, bo pusta strona to poprawna strona.
    EFL i poleasingowe działały tylko dlatego, że akurat wybierały gzip.

    Nagłówek ustawia teraz httpx, więc ogłaszany zbiór z definicji równa się
    zbiorowi dekodowalnemu. Ten test pilnuje, żeby nikt go znów nie nadpisał.
    """
    import httpx

    adapter = registry.utworz(klucz)
    klient = adapter._klient  # type: ignore[attr-defined]
    oglaszane = {
        czesc.split(";")[0].strip().lower()
        for czesc in klient.headers.get("accept-encoding", "").split(",")
        if czesc.strip()
    }
    umiemy = set(httpx._decoders.SUPPORTED_DECODERS) | {"*", "identity"}
    assert (
        oglaszane <= umiemy
    ), f"{klucz} ogłasza {sorted(oglaszane - umiemy)}, czego nie umie rozpakować"


@pytest.mark.parametrize("klucz", KLUCZE)
def test_adapter_nie_wpisuje_accept_encoding_recznie(klucz: str) -> None:
    """SPEC.md §11.3 chce `gzip, br` — i dostaje je, ale od httpx.

    Ręczne wpisanie tego nagłówka jest właśnie tym, co rozjechało ogłoszenie
    z możliwościami. Zbiór dekoderów rozszerza się instalacją `brotli`,
    nie edycją adaptera.
    """
    import inspect

    from app.infrastructure.sources import registry as rejestr

    modul = inspect.getmodule(type(rejestr.utworz(klucz)))
    assert modul is not None
    # Szukamy KLUCZA w słowniku nagłówków, nie samej nazwy — o samym
    # nagłówku wolno pisać w komentarzu, bo to on jest tu tematem.
    assert '"Accept-Encoding":' not in inspect.getsource(modul)
