"""Automatyczna wycena: limit, pauza i kolejka bez bazy i bez sieci."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.application.ports import FabrykaKontekstu
from app.infrastructure import auto_wycena
from app.infrastructure.ai_klienci import (
    Szczyt,
    koniec_szczytu_deepseek,
    szczyt_dostawcy,
)
from app.infrastructure.auto_wycena import (
    LIMIT_WYCZERPANY_S,
    PAUZA_AI_S,
    PAUZA_S,
    PUSTA_KOLEJKA_S,
    AutoWycena,
)
from app.infrastructure.dokumenty import Dokumenty
from app.infrastructure.otomoto import BladDostepuOtomoto, BladOtomoto, Otomoto
from app.infrastructure.supervisor.options import Opcje
from app.infrastructure.wycena_ai import BladDostawcyAI, BladWyceny, WycenaAI
from app.infrastructure.zadania_wyceny import Postep, ZadaniaWyceny
from app.interfaces.main import _auto_wycena

TERAZ = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.UTC)
PRZERWA = 7.0


class Baza:
    """Atrapa fabryki kontekstu: kolejka, licznik prób i zapisane próby."""

    def __init__(self, kolejka: tuple[int, ...] = (), proby_24h: int = 0) -> None:
        self.kolejka = kolejka
        self.proby_24h = proby_24h
        self.zapisane: list[tuple[int, str | None]] = []

    async def proby_wycen_24h(self) -> int:
        return self.proby_24h

    async def kolejka_wycen(self, limit: int) -> tuple[int, ...]:
        return self.kolejka[:limit]

    async def zapisz_probe(
        self, auction_id: int, teraz: dt.datetime, blad: str | None
    ) -> None:
        self.zapisane.append((auction_id, blad))

    async def __aenter__(self) -> Any:
        return SimpleNamespace(wycena=self)

    async def __aexit__(self, *wyjatek: object) -> None:
        return None

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[Any]:
        yield SimpleNamespace(zapytania=self, uow=self)


class Zegar:
    def __init__(self) -> None:
        self.teraz = TERAZ

    def __call__(self) -> dt.datetime:
        return self.teraz


def _automat(
    baza: Baza,
    blad: Exception | None = None,
    *,
    limit: int = 300,
    zadania: ZadaniaWyceny | None = None,
    zegar: Zegar | None = None,
    szczyt: Szczyt | None = None,
    pauza_otomoto_s: float = PAUZA_S,
) -> tuple[AutoWycena, list[int]]:
    wycenione: list[int] = []

    async def praca(auction_id: int, postep: Postep) -> None:
        wycenione.append(auction_id)
        if blad is not None:
            raise blad

    automat = AutoWycena(
        cast(FabrykaKontekstu, baza),
        zadania or ZadaniaWyceny(),
        praca,
        limit_dzienny=limit,
        przerwa_s=PRZERWA,
        pauza_otomoto_s=pauza_otomoto_s,
        zegar=zegar or Zegar(),
        szczyt=szczyt,
    )
    return automat, wycenione


async def test_udana_wycena_zapisuje_probe_i_zdejmuje_zadanie() -> None:
    baza = Baza(kolejka=(5, 6))
    zadania = ZadaniaWyceny()
    automat, wycenione = _automat(baza, zadania=zadania)

    assert await automat.krok() == PRZERWA
    assert wycenione == [5]
    assert baza.zapisane == [(5, None)]
    assert zadania.stan(5) is None


async def test_limit_zatrzymuje_wyceny_i_jest_widoczny() -> None:
    baza = Baza(kolejka=(5,), proby_24h=300)
    automat, wycenione = _automat(baza, limit=300)

    assert await automat.krok() == LIMIT_WYCZERPANY_S
    assert wycenione == []
    assert automat.stan.limit_wyczerpany

    baza.proby_24h = 299
    assert await automat.krok() == PRZERWA
    assert wycenione == [5]
    assert not automat.stan.limit_wyczerpany


async def test_pusta_kolejka_to_dluzszy_sen() -> None:
    automat, wycenione = _automat(Baza())

    assert await automat.krok() == PUSTA_KOLEJKA_S
    assert wycenione == []


@pytest.mark.parametrize(
    "blad",
    [
        BladOtomoto("nie udało się dopasować modelu"),
        BladWyceny("Model zwrócił niespójny przedział wyceny."),
    ],
)
async def test_porazka_jednej_aukcji_nie_zatrzymuje_kolejki(blad: Exception) -> None:
    baza = Baza(kolejka=(5,))
    automat, _ = _automat(baza, blad)

    assert await automat.krok() == PRZERWA
    assert baza.zapisane == [(5, str(blad))]
    assert automat.stan.pauza_do is None


async def test_awaria_dostawcy_ai_wstrzymuje_na_krotko() -> None:
    blad = BladDostawcyAI("Wycena AI nie powiodła się: dostawca odrzucił klucz API")
    baza = Baza(kolejka=(5, 6))
    zegar = Zegar()
    automat, wycenione = _automat(baza, blad, zegar=zegar)

    assert await automat.krok() == PAUZA_AI_S
    assert automat.stan.powod_pauzy == str(blad)
    zegar.teraz += dt.timedelta(seconds=PAUZA_AI_S + 1)
    await automat.krok()
    assert wycenione == [5, 5]


async def test_blokada_otomoto_wstrzymuje_na_godzine() -> None:
    blad = BladDostepuOtomoto("Otomoto nie odpowiada: 429")
    baza = Baza(kolejka=(5, 6))
    zegar = Zegar()
    automat, wycenione = _automat(baza, blad, zegar=zegar)

    assert await automat.krok() == PAUZA_S
    assert automat.stan.powod_pauzy == str(blad)
    # Próba zostaje zapisana: inaczej aukcja, która sama wywołuje taki błąd,
    # stałaby na początku kolejki bez końca.
    assert baza.zapisane == [(5, str(blad))]

    zegar.teraz += dt.timedelta(minutes=10)
    assert await automat.krok() == PAUZA_S - 600
    assert wycenione == [5]

    zegar.teraz += dt.timedelta(minutes=50, seconds=1)
    await automat.krok()
    assert wycenione == [5, 5]


async def test_aukcji_wycenianej_przez_uzytkownika_nie_rusza() -> None:
    baza = Baza(kolejka=(5, 6))
    zadania = ZadaniaWyceny()
    koniec = asyncio.Event()

    async def reczna(postep: Postep) -> None:
        await koniec.wait()

    stan = zadania.uruchom(5, reczna)
    automat, wycenione = _automat(baza, zadania=zadania)

    assert await automat.krok() == PRZERWA
    assert wycenione == [6]
    assert zadania.stan(5) is stan, "wyniku pilnuje karta, nie automat"

    koniec.set()
    assert stan.zadanie is not None
    await stan.zadanie


async def test_awaria_kroku_nie_konczy_petli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_wycena, "PO_AWARII_KROKU_S", 0)
    automat, _ = _automat(Baza())
    drugi = asyncio.Event()
    wywolania = 0

    async def krok() -> float:
        nonlocal wywolania
        wywolania += 1
        if wywolania == 1:
            raise RuntimeError("baza padła")
        drugi.set()
        return 3600.0

    monkeypatch.setattr(automat, "krok", krok)
    zadanie = asyncio.create_task(automat.uruchom())
    await asyncio.wait_for(drugi.wait(), 1)

    zadanie.cancel()
    with pytest.raises(asyncio.CancelledError):
        await zadanie


@pytest.mark.parametrize(
    ("wlaczona", "usluga", "jest"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_automat_powstaje_tylko_z_usluga_i_zgoda(
    wlaczona: bool, usluga: bool, jest: bool
) -> None:
    opcje = Opcje(db_password="x", wycena_auto=wlaczona, wycena_auto_limit_dzienny=120)

    automat = _auto_wycena(
        opcje,
        cast(FabrykaKontekstu, Baza()),
        ZadaniaWyceny(),
        cast(WycenaAI, object()) if usluga else None,
        cast(Otomoto, object()),
        cast(Dokumenty, object()),
    )

    assert (automat is not None) is jest
    if automat is not None:
        assert automat.limit_dzienny == 120


async def test_w_szczycie_dostawcy_nic_nie_wysyla() -> None:
    koniec = TERAZ + dt.timedelta(minutes=30)
    w_szczycie = True

    def szczyt(_: dt.datetime) -> dt.datetime | None:
        return koniec if w_szczycie else None

    baza = Baza(kolejka=(5,))
    automat, wycenione = _automat(baza, szczyt=szczyt)

    assert await automat.krok() == 1800
    assert wycenione == []
    assert baza.zapisane == [], "czekanie nie jest próbą"
    assert automat.stan.szczyt_do == koniec

    w_szczycie = False
    assert await automat.krok() == PRZERWA
    assert wycenione == [5]
    assert automat.stan.szczyt_do is None


def _utc(dzien: int, godzina: int, minuta: int = 0) -> dt.datetime:
    # 2026-11-02 to poniedziałek.
    return dt.datetime(2026, 11, dzien, godzina, minuta, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("chwila", "koniec"),
    [
        (_utc(2, 0, 54), None),
        (_utc(2, 0, 55), _utc(2, 4)),  # zapas przed szczytem
        (_utc(2, 2), _utc(2, 4)),
        (_utc(2, 3, 59), _utc(2, 4)),
        (_utc(2, 4), None),
        (_utc(2, 5, 54), None),
        (_utc(2, 5, 55), _utc(2, 10)),
        (_utc(6, 9, 59), _utc(6, 10)),  # piątek
        (_utc(2, 10), None),
        (_utc(7, 2), None),  # sobota
        (_utc(8, 7), None),  # niedziela
        (_utc(8, 23, 59), None),
    ],
)
def test_godziny_szczytu_deepseeka(
    chwila: dt.datetime, koniec: dt.datetime | None
) -> None:
    assert koniec_szczytu_deepseek(chwila) == koniec


def test_szczyt_liczy_sie_w_utc_a_nie_w_strefie_zapisu() -> None:
    warszawa = dt.timezone(dt.timedelta(hours=1))
    chwila = dt.datetime(2026, 11, 2, 3, 0, tzinfo=warszawa)  # 02:00 UTC

    assert koniec_szczytu_deepseek(chwila) == _utc(2, 4)


@pytest.mark.parametrize(
    ("adres", "regula"),
    [
        ("https://api.deepseek.com/v1", koniec_szczytu_deepseek),
        ("https://api.deepseek.com", koniec_szczytu_deepseek),
        (" https://API.DeepSeek.com/v1 ", koniec_szczytu_deepseek),
        ("https://openrouter.ai/api/v1", None),
        ("https://nie-deepseek.com/v1", None),
        ("http://ollama:11434/v1", None),
        ("", None),
    ],
)
def test_regula_szczytu_tylko_dla_adresu_deepseeka(
    adres: str, regula: Szczyt | None
) -> None:
    assert szczyt_dostawcy(adres) is regula


async def test_pauza_po_blokadzie_otomoto_jest_konfigurowalna() -> None:
    automat, _ = _automat(
        Baza(kolejka=(5,)),
        BladDostepuOtomoto("Otomoto nie odpowiada: 429"),
        pauza_otomoto_s=300,
    )
    assert await automat.krok() == 300
