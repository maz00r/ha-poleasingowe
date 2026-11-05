"""Wycena jednej aukcji z Otomoto — wspólna dla przycisku i automatu (0.34.0).

Wycena jest ta sama bez względu na to, kto ją zlecił: taksonomia i ogłoszenia
Otomoto, ekspertyza i raport autoDNA z dokumentów aukcji, model AI, zapis.
Dwie kopie tej ścieżki rozjechałyby się po cichu i ranking okazji porównywałby
kwoty policzone inaczej niż te na karcie.

Połączenie z bazą jest oddawane na czas Otomoto i modelu AI — to minuty,
a pula ma cztery połączenia (`persistence/pula.py`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from app.application.ports import FabrykaKontekstu
from app.domain.enums import RodzajPojazdu
from app.infrastructure.dokumenty import Dokumenty
from app.infrastructure.ekspertyza import OcenionyDokument, tekst_pdf, wybierz
from app.infrastructure.otomoto import (
    BladOtomoto,
    Otomoto,
    PojazdDoPorownania,
    marka_bazowa,
)
from app.infrastructure.wycena_ai import WycenaAI
from app.infrastructure.zadania_wyceny import Postep

log = logging.getLogger(__name__)

Wybieracz = Callable[
    [PojazdDoPorownania, Sequence[tuple[str, str]]], Awaitable[str | None]
]


class Stoper:
    """Mierzy etapy wyceny, które i tak przechodzą przez `postep`.

    Etap trwa do początku następnego. Dopasowanie modelu przez AI dzieje się
    wewnątrz etapu Otomoto, więc jest podawane osobno i już wliczone.
    """

    def __init__(
        self, postep: Postep, zegar: Callable[[], float] = time.monotonic
    ) -> None:
        self._postep = postep
        self._zegar = zegar
        self._start = zegar()
        self._etap = "Dane z bazy"
        self._poczatek_etapu = self._start
        self._czasy: list[tuple[str, float]] = []
        self._dopasowanie_s = 0.0

    def __call__(self, etap: str, udzial: float) -> None:
        self._zamknij_etap()
        self._etap = etap
        self._postep(etap, udzial)

    def _zamknij_etap(self) -> None:
        teraz = self._zegar()
        self._czasy.append((self._etap, teraz - self._poczatek_etapu))
        self._poczatek_etapu = teraz

    def mierz_dopasowanie(self, wybieracz: Wybieracz) -> Wybieracz:
        async def mierzony(
            pojazd: PojazdDoPorownania, modele: Sequence[tuple[str, str]]
        ) -> str | None:
            poczatek = self._zegar()
            try:
                return await wybieracz(pojazd, modele)
            finally:
                self._dopasowanie_s += self._zegar() - poczatek

        return mierzony

    def opis(self) -> str:
        """Razem i etapy, także dla wyceny przerwanej błędem."""
        self._zamknij_etap()
        etapy = ", ".join(f"{e}: {s:.1f} s" for e, s in self._czasy)
        razem = self._zegar() - self._start
        dopasowanie = (
            f" (w tym dopasowanie modelu przez AI: {self._dopasowanie_s:.1f} s)"
            if self._dopasowanie_s
            else ""
        )
        return f"razem {razem:.1f} s — {etapy}{dopasowanie}"


async def wycen_z_otomoto(
    fabryka: FabrykaKontekstu,
    usluga: WycenaAI,
    otomoto: Otomoto,
    dokumenty: Dokumenty | None,
    auction_id: int,
    postep: Postep,
) -> None:
    """Liczy i zapisuje wycenę. Nieudana próba nie kasuje zapisanej wyceny."""
    stoper = Stoper(postep)
    try:
        await _wycen(fabryka, usluga, otomoto, dokumenty, auction_id, stoper)
    finally:
        log.info("czasy wyceny aukcji %s: %s", auction_id, stoper.opis())


async def _wycen(
    fabryka: FabrykaKontekstu,
    usluga: WycenaAI,
    otomoto: Otomoto,
    dokumenty: Dokumenty | None,
    auction_id: int,
    postep: Stoper,
) -> None:
    async with fabryka() as kontekst:
        dane = await kontekst.zapytania.szczegoly(auction_id)
        porownania = await kontekst.zapytania.porownania_rynkowe(auction_id)
        wystawienia = await kontekst.zapytania.powiazane_wystawienia(auction_id)
    if dane is None:
        raise BladOtomoto("nie ma takiej aukcji")
    p = dane.pozycja
    if not p.make or not p.model:
        raise BladOtomoto("bez marki i modelu nie da się szukać na Otomoto")
    rynek = await otomoto.porownaj(
        PojazdDoPorownania(
            marka=marka_bazowa(p.make),
            model=p.model,
            rok=p.year,
            przebieg_km=p.mileage_km,
            paliwo=p.fuel,
            skrzynia=p.gearbox,
            dostawczy=p.vehicle_kind is RodzajPojazdu.DOSTAWCZY,
            wersja=p.variant,
            tytul=p.nazwa,
        ),
        wybieracz=postep.mierz_dopasowanie(usluga.wybierz_model_otomoto),
        postep=postep,
    )
    wybrane: tuple[OcenionyDokument, ...] = ()
    if dokumenty is not None:
        postep("Pobieranie dokumentów aukcji", 0.55)
        pliki = await dokumenty.pliki_do_analizy(
            auction_id, p.source_key, p.external_id, p.url
        )
        postep("Czytanie ekspertyzy", 0.65)
        teksty = [
            (plik.nazwa, await asyncio.to_thread(tekst_pdf, plik.dane))
            for plik in pliki
        ]
        wybrane = wybierz(teksty, dane.vin)
    postep(f"Model AI liczy wycenę ({rynek.liczba} ogłoszeń)", 0.75)
    nowa = await usluga.wycen(
        dane,
        porownania,
        teraz=dt.datetime.now(dt.UTC),
        otomoto=rynek,
        ekspertyzy=wybrane,
        wystawienia=wystawienia,
    )
    postep("Zapisywanie", 0.95)
    async with fabryka() as kontekst, kontekst.uow as uow:
        await uow.wycena.zapisz(nowa)
