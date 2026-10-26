"""Warstwa antykorupcyjna dawro.pl (RECON.md §4.5).

**Kwoty dawro są brutto** (potwierdzone przez właściciela repo, 2026-10-26;
etykieta „Cena wywoławcza" nie mówi tego wprost). Model porównuje źródła
w PLN netto, więc przeliczamy — ale tylko tam, gdzie VAT rzeczywiście jest:

- „Forma sprzedaży: faktura VAT" → ÷1,23 (`PRZELICZONO_Z_BRUTTO`);
- „umowa k/s" → bez zmian: przy umowie kupna-sprzedaży VAT nie występuje,
  więc brutto i netto to ta sama kwota (`BEZ_VAT`);
- brak pola → jak faktura VAT: większość katalogu to sprzedaż firmowa,
  a pole stoi i na liście, i w szczegółach, więc brak to wyjątek.

Karta pokazuje `vat_info`, bo ta sama liczba znaczy co innego dla obu form.

**Bez paliwa, skrzyni i nadwozia.** Serwis ich nie podaje w żadnym polu;
`fuel`/`gearbox`/`body` zostają `None` — zgadywanie z nazwy modelu byłoby
wymyślaniem faktu (SPEC.md §6.3 — zero magicznych domysłów).
"""

from __future__ import annotations

import datetime as dt
import re
import zoneinfo
from contextlib import suppress
from decimal import ROUND_HALF_UP, Decimal

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency, InformacjaVat
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import kanoniczna_marka, podziel_marke_model
from app.infrastructure.sources.rodzaje import rozpoznaj

STREFA_SERWISU = zoneinfo.ZoneInfo("Europe/Warsaw")

_KONIEC_LISTY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$")
_LICZBA = re.compile(r"\d+")
_MOC = re.compile(r"(\d+)\s*KM", re.I)
_VAT = Decimal("1.23")
_GROSZ = Decimal("0.01")


def _int_lub_none(wartosc: str | None) -> int | None:
    if not wartosc:
        return None
    dopasowanie = _LICZBA.search(wartosc)
    return int(dopasowanie.group()) if dopasowanie is not None else None


def informacja_vat(forma_sprzedazy: str | None) -> InformacjaVat:
    """Umowa kupna-sprzedaży jest bez VAT; wszystko inne to faktura."""
    forma = (forma_sprzedazy or "").lower()
    if "umowa" in forma or "k/s" in forma:
        return InformacjaVat.BEZ_VAT
    return InformacjaVat.PRZELICZONO_Z_BRUTTO


def _cena(wartosc: str | None, vat: InformacjaVat) -> Money | None:
    """Kwota w PLN netto — patrz docstring modułu."""
    if not wartosc:
        return None
    try:
        kwota = Money.z_tekstu(wartosc, Currency.PLN).amount
    except NieprawidlowaWartosc as exc:
        raise ParseFailed(f"dawro: nieprawidłowa kwota: {wartosc!r}") from exc
    if vat is InformacjaVat.PRZELICZONO_Z_BRUTTO:
        kwota = (kwota / _VAT).quantize(_GROSZ, rounding=ROUND_HALF_UP)
    return Money(kwota, Currency.PLN)


def _vin(wartosc: str | None) -> Vin | None:
    if not wartosc:
        return None
    with suppress(NieprawidlowaWartosc):
        return Vin(wartosc)
    return None


def _mileage(wartosc: str | None) -> Mileage | None:
    if not wartosc:
        return None
    with suppress(NieprawidlowaWartosc):
        return Mileage.z_tekstu(wartosc)
    return None


def _koniec_z_listy(wartosc: str | None) -> dt.datetime | None:
    """`Koniec aukcji:` na kafelku — czas lokalny, rozdzielczość minuty."""
    if not wartosc:
        return None
    dopasowanie = _KONIEC_LISTY.match(wartosc.strip())
    if dopasowanie is None:
        raise ParseFailed(f"dawro: nieznany format końca na liście: {wartosc!r}")
    r, m, d, gg, mm = (int(x) for x in dopasowanie.groups())
    return dt.datetime(r, m, d, gg, mm, tzinfo=STREFA_SERWISU).astimezone(dt.UTC)


def _koniec_z_zegara(wartosc: str | None) -> dt.datetime | None:
    """`Zegar.odliczanie(<unix>)` ze szczegółów — sekunda, już w UTC."""
    if not wartosc:
        return None
    try:
        return dt.datetime.fromtimestamp(int(wartosc), tz=dt.UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ParseFailed(f"dawro: nieprawidłowy znacznik czasu: {wartosc!r}") from exc


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję z pozycji listy albo strony szczegółów dawro.pl."""
    pola = surowa.pola

    # Zakończenie wykryte przez `parser.sparsuj_szczegoly` z jawnego,
    # serwerowego tekstu „AUKCJA ZAKOŃCZONA" (RECON.md §4.5) — nie z zaniku
    # ceny. Ten odczyt NIE niesie żadnych danych: cena wywoławcza i
    # najwyższa oferta znikają z HTML-a w tym samym momencie (zmierzone
    # w 32/32 próbkach domknięcia), więc traktowanie go jako zwykły odczyt
    # nadpisałoby jedyną znaną cenę wartością `None` (tak samo jak
    # zniknięcie strony w autoprzetarg.pl — `mapper.py` tego źródła,
    # `pola.get("zniknela")`). Warstwa wyżej (`_scal` w dispatcherze)
    # zachowuje wtedy CAŁĄ resztę aukcji z bazy i tylko zamyka status.
    if pola.get("zamknieta") == "1":
        return Auction(
            source_id=source_id,
            external_id=surowa.external_id,
            url=surowa.url,
            status=AuctionStatus.DISAPPEARED,
            first_seen_at=teraz,
            last_seen_at=teraz,
        )

    nazwa = pola.get("nazwa", "")
    marka, model, wersja = podziel_marke_model(nazwa)
    if marka:
        marka = kanoniczna_marka(marka)

    ends_at = _koniec_z_zegara(pola.get("koniec_ts")) or _koniec_z_listy(
        pola.get("koniec")
    )
    vat = informacja_vat(pola.get("Forma sprzedaży"))

    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=AuctionStatus.ACTIVE,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_int_lub_none(pola.get("Rok produkcji")),
        mileage=_mileage(pola.get("Przebieg")),
        # fuel/gearbox/body zostają None — serwis ich nie podaje (§ modułu).
        engine_ccm=_int_lub_none(pola.get("Pojemność")),
        engine_hp=(
            int(dopasowanie.group(1))
            if (dopasowanie := _MOC.search(pola.get("Moc", ""))) is not None
            else None
        ),
        vin=_vin(pola.get("VIN")),
        location=pola.get("Lokalizacja"),
        seller=pola.get("Sprzedawca") or pola.get("Sprzedający"),
        vehicle_kind=rozpoznaj(kategoria=pola.get("kategoria"), nazwa=nazwa),
        price_start=_cena(pola.get("cena_wywolawcza"), vat),
        # Najwyższa oferta: z listy atrybut `kwota` kafelka, ze szczegółów
        # odpowiedź `PasekInformacyjny` (source.py). Ten sam brutto co cena
        # wywoławcza, więc ta sama reguła VAT.
        price_current=_cena(pola.get("najwyzsza_oferta"), vat),
        vat_info=vat,
        ends_at=ends_at,
        content_hash=surowa.content_hash or None,
    )
