"""Trasy interfejsu operacyjnego (SPEC.md §12).

Add-on obsługuje **operacje**, nie analitykę — stąd brak wykresów i linki
do Grafany zamiast własnych.

Prefiks Ingressu jest dynamiczny, więc każdy adres w szablonach powstaje
przez `url_for`. Ścieżka wpisana na sztywno działa u mnie w testach
i prowadzi donikąd po instalacji.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import pathlib
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.application.read_models import (
    LIMIT_STRONY,
    Diagnostyka,
    Kryteria,
    Sortowanie,
    Strona,
)
from app.domain.entities import SavedFilter, WatchlistEntry
from app.domain.enums import AuthState, Currency
from app.domain.value_objects import Money, NieprawidlowaWartosc
from app.infrastructure.supervisor.proces import rss_bajty
from app.interfaces.web import filtry_szablonu
from app.interfaces.web.formularze import (
    kursor_z_parametrow,
    na_parametry,
    zbuduj_kryteria,
)

log = logging.getLogger(__name__)

KATALOG = pathlib.Path(__file__).resolve().parents[1]
SZABLONY = Jinja2Templates(directory=str(KATALOG / "templates"))
filtry_szablonu.zarejestruj(SZABLONY.env)

CIASTECZKO_WIZYTY = "poleasingowe_ostatnia_wizyta"
DNI_WIZYTY = 90

router = APIRouter()


def _stan(request: Request) -> Any:
    return request.app.state.stan


def _fabryka(request: Request) -> Any:
    """Fabryka kontekstu bazy albo `None`, gdy add-on wstał bez bazy.

    SPEC.md §2 pkt 8: brak bazy nie zatrzymuje procesu. Interfejs ma wtedy
    powiedzieć, **dlaczego** jej nie ma, a nie wywalić się na 500.
    """
    return getattr(request.app.state, "fabryka", None)


def _kontekst_bazowy(request: Request) -> dict[str, Any]:
    stan = _stan(request)
    return {
        "request": request,
        "teraz": dt.datetime.now(dt.UTC),
        "grafana": stan.opcje.grafana_base_url.rstrip("/"),
        "sortowania": list(Sortowanie),
    }


def _brak_bazy(request: Request, kod: int = 503) -> Response:
    stan = _stan(request)
    return SZABLONY.TemplateResponse(
        request=request,
        name="brak-bazy.html",
        context={
            **_kontekst_bazowy(request),
            "powod": stan.ostatni_blad_bazy,
            "polaczenie": stan.opcje.bezpieczny_opis(),
        },
        status_code=kod,
    )


def _ostatnia_wizyta(request: Request) -> dt.datetime | None:
    """Znacznik z ciasteczka dla widoku „nowe od ostatniej wizyty" (§12).

    W ciasteczku, nie w bazie: to stan przeglądarki, a nie fakt o aukcjach.
    Trzymanie go w `app` znaczyłoby, że dwie karty przeglądarki nadpisują
    sobie nawzajem „ostatnią wizytę".
    """
    surowe = request.cookies.get(CIASTECZKO_WIZYTY)
    if not surowe:
        return None
    try:
        wartosc = dt.datetime.fromisoformat(surowe)
    except ValueError:
        return None
    return wartosc if wartosc.tzinfo else wartosc.replace(tzinfo=dt.UTC)


def _zapamietaj_wizyte(odpowiedz: Response, teraz: dt.datetime) -> None:
    odpowiedz.set_cookie(
        CIASTECZKO_WIZYTY,
        teraz.isoformat(),
        max_age=DNI_WIZYTY * 86_400,
        httponly=True,
        samesite="lax",
    )


async def _pobierz_liste(
    request: Request, kryteria: Kryteria
) -> tuple[Strona, dict[str, tuple[str, ...]], list[SavedFilter]]:
    fabryka = _fabryka(request)
    async with fabryka() as kontekst:
        strona = await kontekst.zapytania.lista(
            kryteria, kursor_z_parametrow(request.query_params), LIMIT_STRONY
        )
        wartosci = await kontekst.zapytania.wartosci_filtrow()
        async with kontekst.uow as uow:
            zapisane = list(await uow.saved_filter.wszystkie())
    return strona, wartosci, zapisane


@router.get("/", response_class=HTMLResponse)
async def lista(request: Request) -> Response:
    """Lista z filtrami i paginacją keyset (SPEC.md §12)."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    teraz = dt.datetime.now(dt.UTC)
    kryteria = zbuduj_kryteria(
        request.query_params, ostatnia_wizyta=_ostatnia_wizyta(request)
    )
    strona, wartosci, zapisane = await _pobierz_liste(request, kryteria)

    odpowiedz = SZABLONY.TemplateResponse(
        request=request,
        name="lista.html",
        context={
            **_kontekst_bazowy(request),
            "strona": strona,
            "kryteria": kryteria,
            "parametry": na_parametry(kryteria),
            "wartosci": wartosci,
            "zapisane": zapisane,
        },
    )
    _zapamietaj_wizyte(odpowiedz, teraz)
    return odpowiedz


@router.get("/lista", response_class=HTMLResponse)
async def lista_fragment(request: Request) -> Response:
    """Same wiersze — doładowanie kolejnej strony przez HTMX.

    Ta trasa **nie** odświeża znacznika wizyty: doładowanie drugiej strony
    nie jest nową wizytą, a przestawienie go tutaj kasowałoby widok „nowe"
    w trakcie przeglądania.
    """
    if _fabryka(request) is None:
        return _brak_bazy(request)

    kryteria = zbuduj_kryteria(
        request.query_params, ostatnia_wizyta=_ostatnia_wizyta(request)
    )
    strona, _, _ = await _pobierz_liste(request, kryteria)
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/wiersze.html",
        context={
            **_kontekst_bazowy(request),
            "strona": strona,
            "parametry": na_parametry(kryteria),
        },
    )


@router.get("/aukcja/{auction_id}", response_class=HTMLResponse)
async def szczegoly(request: Request, auction_id: int) -> Response:
    if _fabryka(request) is None:
        return _brak_bazy(request)

    async with _fabryka(request)() as kontekst:
        dane = await kontekst.zapytania.szczegoly(auction_id)
    if dane is None:
        return SZABLONY.TemplateResponse(
            request=request,
            name="nie-znaleziono.html",
            context={**_kontekst_bazowy(request), "auction_id": auction_id},
            status_code=404,
        )
    return SZABLONY.TemplateResponse(
        request=request,
        name="szczegoly.html",
        context={**_kontekst_bazowy(request), "dane": dane},
    )


def _cena_docelowa(surowa: str, waluta: Currency) -> Money | None:
    """Cena z formularza. Pusto znaczy „bez progu", śmieci też."""
    tekst = surowa.strip()
    if not tekst:
        return None
    try:
        return Money(decimal.Decimal(tekst.replace(" ", "").replace(",", ".")), waluta)
    except (decimal.InvalidOperation, NieprawidlowaWartosc):
        return None


@router.post("/aukcja/{auction_id}/obserwuj", response_class=HTMLResponse)
async def obserwuj(
    request: Request,
    auction_id: int,
    notatka: str = Form(default=""),
    cena_docelowa: str = Form(default=""),
) -> Response:
    """Dodanie lub aktualizacja wpisu watchlisty (SPEC.md §12)."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    async with _fabryka(request)() as kontekst:
        async with kontekst.uow as uow:
            await uow.watchlist.dodaj(
                WatchlistEntry(
                    auction_id=auction_id,
                    added_at=dt.datetime.now(dt.UTC),
                    note=notatka.strip() or None,
                    target_price=_cena_docelowa(cena_docelowa, Currency.PLN),
                )
            )
        dane = await kontekst.zapytania.szczegoly(auction_id)
    return _panel_obserwacji(request, dane)


@router.post("/aukcja/{auction_id}/przestan-obserwowac", response_class=HTMLResponse)
async def przestan_obserwowac(request: Request, auction_id: int) -> Response:
    if _fabryka(request) is None:
        return _brak_bazy(request)

    async with _fabryka(request)() as kontekst:
        async with kontekst.uow as uow:
            await uow.watchlist.usun(auction_id)
        dane = await kontekst.zapytania.szczegoly(auction_id)
    return _panel_obserwacji(request, dane)


def _panel_obserwacji(request: Request, dane: Any) -> Response:
    if dane is None:
        return SZABLONY.TemplateResponse(
            request=request,
            name="nie-znaleziono.html",
            context={**_kontekst_bazowy(request), "auction_id": None},
            status_code=404,
        )
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/obserwacja.html",
        context={**_kontekst_bazowy(request), "dane": dane},
    )


@router.post("/filtry")
async def zapisz_filtr(request: Request, nazwa: str = Form()) -> Response:
    """Zapisanie bieżącego zestawu filtrów pod nazwą (SPEC.md §12)."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    kryteria = zbuduj_kryteria(request.query_params)
    czysta = nazwa.strip()
    if czysta:
        async with _fabryka(request)() as kontekst, kontekst.uow as uow:
            await uow.saved_filter.zapisz(
                SavedFilter(
                    name=czysta,
                    criteria=dict(na_parametry(kryteria)),
                    created_at=dt.datetime.now(dt.UTC),
                )
            )
    return RedirectResponse(request.url_for("lista"), status_code=303)


@router.post("/filtry/{filter_id}/usun")
async def usun_filtr(request: Request, filter_id: int) -> Response:
    if _fabryka(request) is None:
        return _brak_bazy(request)
    async with _fabryka(request)() as kontekst, kontekst.uow as uow:
        await uow.saved_filter.usun(filter_id)
    return RedirectResponse(request.url_for("lista"), status_code=303)


@router.get("/diagnostyka", response_class=HTMLResponse)
async def diagnostyka(request: Request) -> Response:
    """Panel diagnostyczny (SPEC.md §12).

    Pola bez źródła danych zostają puste i tak się pokazują. Wpisanie tu
    zera znaczyłoby „zmierzone i wyszło zero", a to nieprawda.
    """
    stan = _stan(request)
    fabryka = _fabryka(request)
    dane = Diagnostyka(
        polaczenie=stan.opcje.bezpieczny_opis(),
        baza_dostepna=stan.baza_dostepna,
        ostatni_blad_bazy=stan.ostatni_blad_bazy,
        rss_bajty=rss_bajty(),
        debug_dumps=stan.opcje.debug_dumps,
    )
    if fabryka is not None:
        async with fabryka() as kontekst:
            zrodla = await kontekst.zapytania.diagnostyka()
            rozmiar = await kontekst.zapytania.rozmiar_bazy()
            czas_bazy = await kontekst.zapytania.czas_serwera()
        dane = Diagnostyka(
            polaczenie=dane.polaczenie,
            baza_dostepna=stan.baza_dostepna,
            ostatni_blad_bazy=stan.ostatni_blad_bazy,
            zrodla=zrodla,
            rozmiar_bazy_bajty=rozmiar,
            rss_bajty=dane.rss_bajty,
            # Dryf liczony względem zegara Postgresa (§11.7). To jedyny
            # autorytatywny zegar, do którego add-on ma dostęp zawsze.
            dryf_zegara_s=(dt.datetime.now(dt.UTC) - czas_bazy).total_seconds(),
            pula=fabryka.stan_puli(),
            debug_dumps=dane.debug_dumps,
        )
    return SZABLONY.TemplateResponse(
        request=request,
        name="diagnostyka.html",
        context={**_kontekst_bazowy(request), "dane": dane},
    )


@router.post("/zrodlo/{key}/odblokuj")
async def odblokuj_zrodlo(request: Request, key: str) -> Response:
    """Reset licznika `AUTH_LOCKED` (SPEC.md §10.2, §12).

    Świadoma decyzja operatora, nie automat: blokada po trzech nieudanych
    logowaniach istnieje po to, żeby add-on nie zablokował konta w serwisie.
    Po resecie stan to `EXPIRED`, a nie `OK` — sesji jeszcze nie ma.
    """
    if _fabryka(request) is None:
        return _brak_bazy(request)

    from dataclasses import replace

    async with _fabryka(request)() as kontekst, kontekst.uow as uow:
        zrodlo = await uow.source.po_kluczu(key)
        if zrodlo is not None:
            await uow.source.zapisz(
                replace(
                    zrodlo, auth_state=AuthState.EXPIRED, consecutive_auth_failures=0
                )
            )
            log.info(
                "odblokowano źródło %s — licznik nieudanych logowań wyzerowany", key
            )
    return RedirectResponse(request.url_for("diagnostyka"), status_code=303)
