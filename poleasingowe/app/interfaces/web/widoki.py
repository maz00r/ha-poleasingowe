"""Trasy interfejsu operacyjnego (SPEC.md §12).

Add-on obsługuje **operacje**, nie analitykę — stąd brak wykresów i linki
do Grafany zamiast własnych.

Prefiks Ingressu jest dynamiczny. Dokument ustawia względny element `base`,
a każdy adres powstaje przez `sciezka` względem korzenia aplikacji. Nie
musimy dzięki temu znać tokenu Ingressu ani ufać, że każde proxy przekaże
niestandardowy nagłówek.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import pathlib
import posixpath
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.application.read_models import (
    ETYKIETY_SORTOWANIA,
    LIMIT_STRONY,
    SORTOWANIE_KOLUMN,
    Diagnostyka,
    Kryteria,
    Sortowanie,
    Strona,
)
from app.domain.entities import SavedFilter, WatchlistEntry
from app.domain.enums import Currency, PollTier
from app.domain.logowanie import (
    StanLogowania,
    po_recznym_odblokowaniu,
)
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


def sciezka(request: Request, nazwa: str, **parametry: object) -> str:
    """Ścieżka względem korzenia aplikacji, nigdy względem origin.

    Home Assistant ukrywa add-on pod dynamicznym prefiksem Ingressu. Nie
    próbujemy tego prefiksu odtwarzać z nagłówka: CSS i HTMX mają działać
    również wtedy, gdy pośrednie proxy go nie przekaże. Element ``<base>``
    w ``base.html`` ustawia przeglądarce korzeń aplikacji, a wszystkie adresy
    zwracane tutaj są względem tego korzenia.

    To działa także dla fragmentów HTMX: po wstawieniu fragmentu jego adresy
    nadal rozwiązują się względem ``<base>`` głównego dokumentu, nie względem
    trasy, która zwróciła fragment.
    """
    sciezka_docelowa = str(request.app.url_path_for(nazwa, **parametry))
    return sciezka_docelowa.lstrip("/") or "./"


def baza_sciezek(request: Request) -> str:
    """Względne dojście z bieżącej strony do korzenia aplikacji.

    Proxy Ingressu zdejmuje swój prefiks przed przekazaniem żądania, ale jego
    końcówka jest taka sama jak ``request.url.path``. Dzięki temu ``../`` z
    karty ``/aukcja/123`` prowadzi do korzenia add-onu zarówno lokalnie, jak
    i pod ``/api/hassio_ingress/<token>/`` — bez znajomości tokenu.
    """
    katalog = posixpath.dirname(request.url.path)
    wzgledna = posixpath.relpath("/", start=katalog)
    return f"{wzgledna.rstrip('/')}/"


def sciezka_przekierowania(request: Request, nazwa: str, **parametry: object) -> str:
    """Location względne wobec bieżącego żądania HTTP.

    Nagłówek ``Location`` nie korzysta z HTML-owego ``<base>``, więc dla
    odpowiedzi 303 liczymy drogę osobno. Przeglądarka zachowuje przy tym
    niewidoczny dla aplikacji prefiks Ingressu.
    """
    cel = str(request.app.url_path_for(nazwa, **parametry))
    katalog = posixpath.dirname(request.url.path)
    wzgledna = posixpath.relpath(cel, start=katalog)
    return f"{wzgledna}/" if cel.endswith("/") else wzgledna


# Globalne w szablonach — `url_for` jest tu nie do uzycia, patrz wyzej.
SZABLONY.env.globals["sciezka"] = sciezka
SZABLONY.env.globals["baza_sciezek"] = baza_sciezek


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
        "etykiety_sortowania": ETYKIETY_SORTOWANIA,
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


ZAAWANSOWANE = (
    "model",
    "paliwo",
    "skrzynia",
    "lokalizacja",
    "cena_od",
    "cena_do",
    "rocznik_od",
    "rocznik_do",
    "przebieg_do",
    "konczy_sie_w_h",
    "tylko_obserwowane",
    "nowe_od",
)


def _czy_rozwinac_filtry(kryteria: Kryteria) -> bool:
    """Sekcja „więcej filtrów" ma być otwarta, gdy coś w niej działa.

    Zwinięty filtr, który cicho zawęża listę, to najgorszy rodzaj filtra:
    użytkownik widzi za mało wyników i nie ma jak zgadnąć dlaczego.
    """
    return any(getattr(kryteria, pole) for pole in ZAAWANSOWANE)


async def _pobierz_liste(
    request: Request, kryteria: Kryteria
) -> tuple[Strona, dict[str, tuple[str, ...]], list[SavedFilter], bool]:
    fabryka = _fabryka(request)
    async with fabryka() as kontekst:
        strona = await kontekst.zapytania.lista(
            kryteria, kursor_z_parametrow(request.query_params), LIMIT_STRONY
        )
        wartosci = await kontekst.zapytania.wartosci_filtrow()
        # Pytamy tylko wtedy, gdy lista wyszła pusta — inaczej to zbędne
        # zapytanie przy każdym wejściu na stronę.
        cokolwiek = (
            True
            if strona.pozycje
            else await kontekst.zapytania.sa_jakiekolwiek_aukcje()
        )
        async with kontekst.uow as uow:
            zapisane = list(await uow.saved_filter.wszystkie())
    return strona, wartosci, zapisane, cokolwiek


def _linki_sortowania(kryteria: Kryteria) -> dict[str, dict[str, str]]:
    """Dla każdej sortowalnej kolumny: dokąd prowadzi klik i czy jest aktywna.

    Klik w aktywną kolumnę odwraca kierunek — tak działa każda tabela, którą
    użytkownik już zna. Filtry zostają, bo idą tym samym adresem.
    """
    parametry = na_parametry(kryteria)
    wynik: dict[str, dict[str, str]] = {}
    for kolumna, (rosnaco, malejaco) in SORTOWANIE_KOLUMN.items():
        aktywna = kryteria.sortowanie in (rosnaco, malejaco)
        nastepne = malejaco if kryteria.sortowanie is rosnaco else rosnaco
        wynik[kolumna] = {
            "sort": nastepne.value,
            "parametry": urlencode({**parametry, "sort": nastepne.value}),
            "strzalka": ("↑" if kryteria.sortowanie is rosnaco else "↓")
            if aktywna
            else "",
        }
    return wynik


@router.get("/", response_class=HTMLResponse)
async def lista(request: Request) -> Response:
    """Lista z filtrami i paginacją keyset (SPEC.md §12)."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    teraz = dt.datetime.now(dt.UTC)
    kryteria = zbuduj_kryteria(
        request.query_params, ostatnia_wizyta=_ostatnia_wizyta(request)
    )
    strona, wartosci, zapisane, cokolwiek = await _pobierz_liste(request, kryteria)

    odpowiedz = SZABLONY.TemplateResponse(
        request=request,
        name="lista.html",
        context={
            **_kontekst_bazowy(request),
            "strona": strona,
            "kryteria": kryteria,
            "parametry": na_parametry(kryteria),
            "sortowanie_kolumn": _linki_sortowania(kryteria),
            "wartosci": wartosci,
            "zapisane": zapisane,
            "rozwin_filtry": _czy_rozwinac_filtry(kryteria),
            "pusta_baza": not cokolwiek,
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
    strona, _, _, _ = await _pobierz_liste(request, kryteria)
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
            # SPEC.md §11.2 — pojedynczo odpytujemy WYŁĄCZNIE obserwowane.
            # Bez tego dodanie do watchlisty niczego by nie zmieniało:
            # aukcja z pustym `next_poll_at` nigdy nie trafia do kolejki
            # dispatchera, więc jej cena stałaby na wartości z przemiatu.
            await uow.auction.zaplanuj(
                auction_id, dt.datetime.now(dt.UTC), PollTier.FAR
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
            # Koniec obserwacji to koniec pojedynczych odpytów — dalej
            # wystarcza zbiorczy przemiat listy (§11.2).
            await uow.auction.zaplanuj(auction_id, None, PollTier.IDLE)
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


@router.get("/aukcja/{auction_id}/zdjecia", response_class=HTMLResponse)
async def zdjecia_aukcji(request: Request, auction_id: int) -> Response:
    """Galeria doładowywana po wyrenderowaniu karty (SPEC.md §12).

    Osobne żądanie, bo pobranie adresów wymaga odpytania strony źródła —
    trzymanie na to karty aukcji znaczyłoby, że wolny serwis zatrzymuje
    również dane, które mamy już w bazie.
    """
    galeria = getattr(request.app.state, "galeria", None)
    if galeria is None or _fabryka(request) is None:
        return HTMLResponse("")

    async with _fabryka(request)() as kontekst:
        dane = await kontekst.zapytania.szczegoly(auction_id)
    if dane is None:
        return HTMLResponse("")

    adresy = await galeria.adresy(dane.pozycja.source_key, dane.pozycja.external_id)
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/zdjecia.html",
        context={
            **_kontekst_bazowy(request),
            "auction_id": auction_id,
            "ile": len(adresy),
        },
    )


@router.get("/aukcja/{auction_id}/zdjecie/{indeks}")
async def zdjecie(request: Request, auction_id: int, indeks: int) -> Response:
    """Pojedyncze zdjęcie z cache'u na dysku (SPEC.md §12 — nie hotlink).

    Trasa przyjmuje **indeks**, nie adres. Gdyby przyjmowała adres, add-on
    byłby otwartym proxy: każdy, kto dosięgnie panelu, mógłby przez niego
    odpytywać dowolne adresy w sieci lokalnej.
    """
    galeria = getattr(request.app.state, "galeria", None)
    if galeria is None or _fabryka(request) is None:
        return Response(status_code=404)

    async with _fabryka(request)() as kontekst:
        dane = await kontekst.zapytania.szczegoly(auction_id)
    if dane is None:
        return Response(status_code=404)

    wynik = await galeria.obraz(
        dane.pozycja.source_key, dane.pozycja.external_id, indeks
    )
    if wynik is None:
        return Response(status_code=404)
    tresc, typ = wynik
    # Zdjęcie aukcji nie zmienia się w trakcie jej trwania, a add-on i tak
    # trzyma je na dysku — niech przeglądarka nie pyta o nie przy każdym
    # otwarciu karty.
    return Response(tresc, media_type=typ, headers={"Cache-Control": "max-age=86400"})


@router.post("/aukcja/{auction_id}/przelacz", response_class=HTMLResponse)
async def przelacz_obserwacje(request: Request, auction_id: int) -> Response:
    """Obserwuj / przestań, jednym kliknięciem z listy (SPEC.md §12).

    Decyzja „obserwuję to" zapada przy przeglądaniu listy, a nie po wejściu
    w szczegóły — zmuszanie do dwóch przeładowań na każdą pozycję czyni
    watchlistę bezużyteczną przy kilkudziesięciu aukcjach.
    """
    if _fabryka(request) is None:
        return _brak_bazy(request)

    async with _fabryka(request)() as kontekst:
        async with kontekst.uow as uow:
            if await uow.watchlist.obserwowana(auction_id):
                await uow.watchlist.usun(auction_id)
                await uow.auction.zaplanuj(auction_id, None, PollTier.IDLE)
            else:
                await uow.watchlist.dodaj(
                    WatchlistEntry(
                        auction_id=auction_id, added_at=dt.datetime.now(dt.UTC)
                    )
                )
                await uow.auction.zaplanuj(
                    auction_id, dt.datetime.now(dt.UTC), PollTier.FAR
                )
        dane = await kontekst.zapytania.szczegoly(auction_id)

    if dane is None:
        return HTMLResponse("", status_code=404)
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/gwiazdka.html",
        context={**_kontekst_bazowy(request), "p": dane.pozycja},
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
    return RedirectResponse(sciezka_przekierowania(request, "lista"), status_code=303)


@router.post("/filtry/{filter_id}/usun")
async def usun_filtr(request: Request, filter_id: int) -> Response:
    if _fabryka(request) is None:
        return _brak_bazy(request)
    async with _fabryka(request)() as kontekst, kontekst.uow as uow:
        await uow.saved_filter.usun(filter_id)
    return RedirectResponse(sciezka_przekierowania(request, "lista"), status_code=303)


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
            # Przez regułę domenową, nie przez ręczne ustawienie kolumn:
            # inaczej panel i dispatcher mogłyby rozumieć „odblokowanie"
            # inaczej, a to jest dokładnie ta reguła, która chroni konto
            # w serwisie przed zablokowaniem (SPEC.md §10.2).
            po = po_recznym_odblokowaniu(
                StanLogowania(
                    stan=zrodlo.auth_state,
                    nieudane_proby=zrodlo.consecutive_auth_failures,
                )
            )
            await uow.source.zapisz(
                replace(
                    zrodlo,
                    auth_state=po.stan,
                    consecutive_auth_failures=po.nieudane_proby,
                )
            )
            log.info(
                "odblokowano źródło %s — licznik nieudanych logowań wyzerowany", key
            )
    return RedirectResponse(
        sciezka_przekierowania(request, "diagnostyka"), status_code=303
    )
