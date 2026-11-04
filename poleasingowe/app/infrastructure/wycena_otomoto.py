"""Wycena jednej aukcji z Otomoto — wspólna dla przycisku i automatu (0.34.0).

Wycena jest ta sama bez względu na to, kto ją zlecił: taksonomia i ogłoszenia
Otomoto, ekspertyza z dokumentów aukcji, model AI, zapis. Dwie kopie tej
ścieżki rozjechałyby się po cichu i ranking okazji porównywałby kwoty
policzone inaczej niż te na karcie.

Połączenie z bazą jest oddawane na czas Otomoto i modelu AI — to minuty,
a pula ma cztery połączenia (`persistence/pula.py`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace

from app.application.ports import FabrykaKontekstu
from app.domain.enums import RodzajPojazdu
from app.infrastructure.dokumenty import Dokumenty
from app.infrastructure.ekspertyza import (
    OcenionyDokument,
    tekst_pdf,
    wybierz,
    zredaguj,
)
from app.infrastructure.otomoto import BladOtomoto, Otomoto, PojazdDoPorownania
from app.infrastructure.wycena_ai import WycenaAI
from app.infrastructure.zadania_wyceny import Postep


async def wycen_z_otomoto(
    fabryka: FabrykaKontekstu,
    usluga: WycenaAI,
    otomoto: Otomoto,
    dokumenty: Dokumenty | None,
    auction_id: int,
    postep: Postep,
) -> None:
    """Liczy i zapisuje wycenę. Nieudana próba nie kasuje zapisanej wyceny."""
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
            marka=p.make,
            model=p.model,
            rok=p.year,
            przebieg_km=p.mileage_km,
            paliwo=p.fuel,
            skrzynia=p.gearbox,
            dostawczy=p.vehicle_kind is RodzajPojazdu.DOSTAWCZY,
            wersja=p.variant,
            tytul=p.nazwa,
        ),
        wybieracz=usluga.wybierz_model_otomoto,
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
        wybrane = tuple(
            replace(d, tekst=zredaguj(d.tekst, dane.vin))
            for d in wybierz(teksty, dane.vin)
        )
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
