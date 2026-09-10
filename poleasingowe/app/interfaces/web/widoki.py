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
import logging
import pathlib
import posixpath
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from app.application.read_models import (
    ETYKIETY_RODZAJU,
    ETYKIETY_SORTOWANIA,
    LIMIT_STRONY,
    SORTOWANIE_KOLUMN,
    Diagnostyka,
    Kryteria,
    Kursor,
    Sortowanie,
    Strona,
    Zakres,
)
from app.domain.entities import SavedFilter, WatchlistEntry
from app.domain.enums import AuctionStatus, PollTier
from app.domain.logowanie import (
    StanLogowania,
    po_recznym_odblokowaniu,
)
from app.infrastructure.supervisor.proces import rss_bajty
from app.infrastructure.wycena_ai import BladWyceny
from app.interfaces.web import eksport, filtry_szablonu
from app.interfaces.web.formularze import (
    PARAMETR_ZE_STATUSU,
    kursor_z_parametrow,
    na_parametry,
    zbuduj_kryteria,
)
from app.wersja import WERSJA

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
PRZERWA_WIZYTY_S = 30 * 60
"""Po tylu sekundach bezczynności zaczyna się NOWA wizyta.

Pół godziny: dość długo, żeby przerwa na kawę nie przewinęła
znacznika, i dość krótko, żeby wieczorne wejście po porannym
liczyło się jako osobne."""

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
        "etykiety_rodzaju": ETYKIETY_RODZAJU,
        "wersja": WERSJA,
        "zakladki": _zakladki(request),
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


def _czas_z_ciasteczka(surowe: str) -> dt.datetime | None:
    try:
        wartosc = dt.datetime.fromisoformat(surowe)
    except ValueError:
        return None
    return wartosc if wartosc.tzinfo else wartosc.replace(tzinfo=dt.UTC)


def _wizyta(request: Request) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Znacznik „od kiedy nowe" oraz czas ostatniej aktywności (§12).

    W ciasteczku, nie w bazie: to stan przeglądarki, a nie fakt o aukcjach.
    Trzymanie go w `app` znaczyłoby, że dwie karty przeglądarki nadpisują
    sobie nawzajem „ostatnią wizytę".

    DWIE WARTOŚCI, NIE JEDNA. Wcześniej ciasteczko trzymało samo „teraz"
    i było nadpisywane przy **każdym** wyświetleniu listy — łącznie z tym,
    na którym stał filtr „nowe od ostatniej wizyty". Znacznik cofał się więc
    o kilka sekund przed samym siebie i widok był pusty zawsze, niezależnie
    od tego, ile aukcji naprawdę doszło.
    """
    surowe = request.cookies.get(CIASTECZKO_WIZYTY)
    if not surowe:
        return None, None
    znacznik, _, aktywnosc = surowe.partition("|")
    return _czas_z_ciasteczka(znacznik), _czas_z_ciasteczka(aktywnosc)


def _zapamietaj_wizyte(
    odpowiedz: Response, request: Request, teraz: dt.datetime
) -> None:
    """Przesuwa znacznik dopiero przy NOWEJ wizycie, nie przy każdej stronie.

    Wizyta kończy się po `PRZERWA_WIZYTY_S` bezczynności. Dopóki trwa,
    znacznik stoi w miejscu — dzięki temu „nowe od ostatniej wizyty" znaczy
    to samo przez całe przeglądanie, a nie „nowe od poprzedniego kliknięcia".
    """
    znacznik, aktywnosc = _wizyta(request)
    if aktywnosc is None:
        # Pierwsze wejście: nie ma jeszcze poprzedniej wizyty, więc nie ma
        # też sensownego „od kiedy". Zapisujemy `teraz` i widok zacznie
        # działać od następnej sesji.
        znacznik = teraz
    elif (teraz - aktywnosc).total_seconds() > PRZERWA_WIZYTY_S:
        # Nowa wizyta. „Nowe" liczymy od końca poprzedniej.
        znacznik = aktywnosc
    odpowiedz.set_cookie(
        CIASTECZKO_WIZYTY,
        f"{(znacznik or teraz).isoformat()}|{teraz.isoformat()}",
        max_age=DNI_WIZYTY * 86_400,
        httponly=True,
        samesite="lax",
    )


ZAAWANSOWANE = (
    "model",
    "paliwa",
    "skrzynie",
    "lokalizacje",
    "cena_od",
    "cena_do",
    "rocznik_od",
    "rocznik_do",
    "moc_od",
    "moc_do",
    "przebieg_do",
    "konczy_sie_w_h",
    "tylko_obserwowane",
    "nowe_od",
)


def _ile_zaawansowanych(kryteria: Kryteria) -> int:
    """Ile filtrów działa w zwiniętej sekcji „więcej filtrów".

    Sekcja jest **zawsze zwinięta** — otwieranie jej zależnie od zawartości
    sprawiało, że pasek filtrów skakał między zakładkami i nie dało się
    zapamiętać, gdzie co stoi.

    Ale zwinięty filtr, który cicho zawęża listę, to najgorszy rodzaj filtra:
    widać za mało wyników i nie ma jak zgadnąć dlaczego. Dlatego zamiast
    otwierać sekcję, pokazujemy przy niej **licznik** — informacja zostaje,
    a układ strony przestaje się ruszać.
    """
    return sum(1 for pole in ZAAWANSOWANE if getattr(kryteria, pole))


ZAKLADKI = (
    ("aktywne", "Aktywne", {"status": "aktywne"}),
    ("konczace", "Kończą się w 24 h", {"status": "aktywne", "do_konca_h": "24"}),
    ("nowe", "Nowe od ostatniej wizyty", {"status": "aktywne", "nowe": "1"}),
    ("obserwowane", "Obserwowane", {"obserwowane": "1", "status": "aktywne"}),
    (
        "ponownie",
        "Wystawione ponownie",
        {"status": "aktywne", "ponownie": "1", "sort": "koniec"},
    ),
    (
        "archiwum",
        "Archiwum",
        {"status": "zakonczone", "obserwowane": "1", "sort": "koniec-desc"},
    ),
)
"""Zakładki paska — **jedno źródło prawdy** dla adresu i dla podświetlenia.

Wcześniej adresy stały wpisane w szablonie, więc nie było czym rozpoznać
bieżącej zakładki: żadne miejsce w kodzie nie wiedziało, że te sześć linków
tworzy jakikolwiek zbiór.
"""


def _rozpoznaj_zakladke(request: Request) -> str | None:
    """Która zakładka jest otwarta — po znaczących parametrach, nie po adresie.

    Porównujemy **znormalizowane kryteria**, a nie napisy w URL-u, więc
    dołożenie marki czy zmiana sortowania nie gasi podświetlenia: to wciąż ta
    sama zakładka, tylko zawężona. Rozstrzygają wyłącznie te pola, które
    odróżniają zakładki od siebie.
    """
    if request.url.path.rstrip("/").endswith("/diagnostyka"):
        return "diagnostyka"

    def klucz(parametry: Mapping[str, str]) -> tuple[Any, ...]:
        k = zbuduj_kryteria(parametry)
        return (
            k.status,
            k.tylko_obserwowane,
            k.tylko_wystawione_ponownie,
            k.konczy_sie_w_h,
            "nowe" in parametry,
        )

    biezacy = klucz(request.query_params)
    for nazwa, _etykieta, parametry in ZAKLADKI:
        if klucz(parametry) == biezacy:
            return nazwa
    return None


def _zakladki(request: Request) -> list[dict[str, Any]]:
    aktywna = _rozpoznaj_zakladke(request)
    return [
        {
            "etykieta": etykieta,
            "parametry": list(parametry.items()),
            "aktywna": nazwa == aktywna,
        }
        for nazwa, etykieta, parametry in ZAKLADKI
    ] + [
        {
            "etykieta": "Diagnostyka",
            "parametry": None,
            "aktywna": aktywna == "diagnostyka",
        }
    ]


@dataclass(slots=True, frozen=True)
class _DaneListy:
    """Wszystko, czego potrzebuje widok listy, z jednego przejścia po bazie."""

    strona: Strona
    wartosci: dict[str, tuple[str, ...]]
    zakresy: dict[str, Zakres]
    zapisane: list[SavedFilter]
    cokolwiek: bool


async def _pobierz_liste(request: Request, kryteria: Kryteria) -> _DaneListy:
    fabryka = _fabryka(request)
    async with fabryka() as kontekst:
        strona = await kontekst.zapytania.lista(
            kryteria, kursor_z_parametrow(request.query_params), LIMIT_STRONY
        )
        wartosci = await kontekst.zapytania.wartosci_filtrow()
        zakresy = await kontekst.zapytania.zakresy_filtrow()
        # Pytamy tylko wtedy, gdy lista wyszła pusta — inaczej to zbędne
        # zapytanie przy każdym wejściu na stronę.
        cokolwiek = (
            True
            if strona.pozycje
            else await kontekst.zapytania.sa_jakiekolwiek_aukcje()
        )
        async with kontekst.uow as uow:
            zapisane = list(await uow.saved_filter.wszystkie())
    return _DaneListy(strona, wartosci, zakresy, zapisane, cokolwiek)


def _parametr_statusu(kryteria: Kryteria) -> str:
    """Nazwa statusu dla listy rozwijanej. Osobno, bo szablon nie ma już
    słownika parametrów — filtry idą listą par."""
    return (
        "wszystkie" if kryteria.status is None else PARAMETR_ZE_STATUSU[kryteria.status]
    )


def _kryteria_do_zapisu(kryteria: Kryteria) -> dict[str, list[str]]:
    """Filtry w postaci nadającej się do `jsonb` — z zachowaniem powtórzeń.

    `dict(pary)` gubiłby tu wszystko poza ostatnią wartością klucza, czyli
    zapisany filtr „diesel albo benzyna" wracałby jako sama benzyna.
    """
    zgrupowane: dict[str, list[str]] = {}
    for klucz, wartosc in na_parametry(kryteria):
        zgrupowane.setdefault(klucz, []).append(wartosc)
    return zgrupowane


PRZELACZNIK_STATUSU = (
    ("aktywne", "Aktywne", AuctionStatus.ACTIVE),
    ("zakonczone", "Wygasłe", AuctionStatus.ENDED),
    ("wszystkie", "Wszystkie", None),
)


def _przelacznik_statusu(kryteria: Kryteria) -> list[dict[str, Any]]:
    """Aktywne / wygasłe / wszystkie — dla widoków zawężonych (§12).

    Pokazujemy go tylko tam, gdzie lista odpowiada na jedno konkretne pytanie
    („co obserwuję", „co wróciło na aukcję"). Wtedy podział na trwające
    i wygasłe jest naturalnym drugim pytaniem, a schowany jest dziś w liście
    rozwijanej wśród czternastu innych filtrów.

    Każda pozycja niesie **komplet** bieżących parametrów z podmienionym
    statusem — przełączenie nie może gubić reszty filtrów ani sortowania.
    """
    wynik: list[dict[str, Any]] = []
    for wartosc, etykieta, status in PRZELACZNIK_STATUSU:
        parametry = na_parametry(replace(kryteria, status=status))
        wynik.append(
            {
                "etykieta": etykieta,
                "parametry": parametry,
                "aktywny": kryteria.status is status,
                "wartosc": wartosc,
            }
        )
    return wynik


def _linki_sortowania(kryteria: Kryteria) -> dict[str, dict[str, str | bool]]:
    """Dla każdego klucza sortowania: dokąd prowadzi klik i czy jest aktywny.

    Klik w aktywny klucz odwraca kierunek — tak działa każda lista, którą
    użytkownik już zna. Filtry zostają, bo idą tym samym adresem.

    `aktywna` wychodzi na zewnątrz, bo w siatce kafelków nie ma nagłówków
    kolumn: bieżący klucz musi być widoczny sam z siebie, inaczej nie
    wiadomo, po czym lista jest ułożona.
    """
    parametry = na_parametry(kryteria)
    # Filtry wielokrotnego wyboru powtarzają klucz, więc pracujemy na liście
    # par: podmiana sortowania to wyrzucenie starego `sort` i dopisanie
    # nowego, a nie nadpisanie klucza w słowniku.
    bez_sortu = [(k, w) for k, w in parametry if k != "sort"]
    wynik: dict[str, dict[str, str | bool]] = {}
    for kolumna, (rosnaco, malejaco) in SORTOWANIE_KOLUMN.items():
        aktywna = kryteria.sortowanie in (rosnaco, malejaco)
        nastepne = malejaco if kryteria.sortowanie is rosnaco else rosnaco
        wynik[kolumna] = {
            "sort": nastepne.value,
            "parametry": urlencode([*bez_sortu, ("sort", nastepne.value)]),
            "strzalka": ("↑" if kryteria.sortowanie is rosnaco else "↓")
            if aktywna
            else "",
            "aktywna": aktywna,
        }
    return wynik


@router.get("/", response_class=HTMLResponse)
async def lista(request: Request) -> Response:
    """Lista z filtrami i paginacją keyset (SPEC.md §12)."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    teraz = dt.datetime.now(dt.UTC)
    kryteria = zbuduj_kryteria(
        request.query_params, ostatnia_wizyta=_wizyta(request)[0]
    )
    dane = await _pobierz_liste(request, kryteria)

    odpowiedz = SZABLONY.TemplateResponse(
        request=request,
        name="lista.html",
        context={
            **_kontekst_bazowy(request),
            "strona": dane.strona,
            "kryteria": kryteria,
            "parametry": na_parametry(kryteria),
            "sortowanie_kolumn": _linki_sortowania(kryteria),
            "wartosci": dane.wartosci,
            "zakresy": dane.zakresy,
            "zapisane": dane.zapisane,
            "ile_zaawansowanych": _ile_zaawansowanych(kryteria),
            "pusta_baza": not dane.cokolwiek,
            "parametr_statusu": _parametr_statusu(kryteria),
            "przelacznik_statusu": (
                _przelacznik_statusu(kryteria)
                if kryteria.tylko_obserwowane or kryteria.tylko_wystawione_ponownie
                else None
            ),
        },
    )
    _zapamietaj_wizyte(odpowiedz, request, teraz)
    return odpowiedz


MAKS_EKSPORTU = 20_000
"""Sufit wierszy eksportu. Przy 170 MB RSS (§1.1) nie ma miejsca na
nieograniczone pobranie, a dwadzieścia tysięcy aukcji to i tak więcej,
niż którykolwiek z serwisów wystawia naraz."""


@router.get("/eksport.csv")
async def eksport_csv(request: Request) -> Response:
    """Bieżąca lista jako CSV — te same filtry, co na ekranie (§14 pkt 11).

    Strumieniowo: kolejne strony ciągniemy kursorem keyset i oddajemy od
    razu, zamiast budować cały plik w pamięci procesu.
    """
    if _fabryka(request) is None:
        return _brak_bazy(request)

    kryteria = zbuduj_kryteria(
        request.query_params, ostatnia_wizyta=_wizyta(request)[0]
    )
    teraz = dt.datetime.now(dt.UTC)

    async def pozycje() -> AsyncIterator[Any]:
        kursor = None
        oddane = 0
        async with _fabryka(request)() as kontekst:
            while oddane < MAKS_EKSPORTU:
                strona = await kontekst.zapytania.lista(kryteria, kursor, LIMIT_STRONY)
                for pozycja in strona.pozycje:
                    yield pozycja
                    oddane += 1
                if not strona.ma_wiecej or strona.kursor_dalej is None:
                    return
                kursor = Kursor.odkoduj(strona.kursor_dalej)

    async def linie() -> AsyncIterator[bytes]:
        yield eksport.naglowek().encode("utf-8")
        async for pozycja in pozycje():
            yield eksport.linia_pozycji(pozycja).encode("utf-8")

    return StreamingResponse(
        linie(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{eksport.nazwa_pliku(teraz)}"'
            )
        },
    )


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
        request.query_params, ostatnia_wizyta=_wizyta(request)[0]
    )
    dane = await _pobierz_liste(request, kryteria)
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/karty.html",
        context={
            **_kontekst_bazowy(request),
            "strona": dane.strona,
            "parametry": na_parametry(kryteria),
        },
    )


ODSWIEZ_PO_S = 60
"""Poniżej tej świeżości nie prosimy o nic — dane sprzed minuty są świeże.

Ten sam próg co domyślny floor odpytu (§11.2): karta nie ma prawa generować
ruchu gęstszego niż harmonogram, bo odświeżanie z przeglądarki jest jedyną
ścieżką, w której o tempie decyduje człowiek, a nie pętla.
"""

MAKS_PROB_ODSWIEZENIA = 6
"""Ile razy karta pyta, czy przyszedł świeży odczyt.

Sześć prób po ~2 s to kilkanaście sekund — dłużej niż trwa jeden odpyt,
a krócej, niż ktokolwiek zechce patrzeć na kręcące się kółko. Po nich pasek
znika i zostaje to, co mamy; brak świeżych danych też jest odpowiedzią.
"""

OPOZNIENIE_PROBY_S = 2


def _budzik(request: Request) -> None:
    """Prosi pętlę dyspozytora o wcześniejszy obrót, jeśli w ogóle działa.

    Brak budzika to normalny stan — interfejs wstaje też bez pętli (§2 pkt 8)
    i wtedy dane po prostu odświeżą się przy następnym przemiacie.
    """
    budzik = getattr(request.app.state, "budzik", None)
    if budzik is not None:
        budzik.obudz()


def _stan_odswiezania(
    dane: Any, od: dt.datetime | None, proba: int
) -> dict[str, Any] | None:
    """Czy karta ma dalej czekać na świeży odczyt — i z jakim opóźnieniem.

    `None` znaczy „przestań pytać": albo dane już przyszły, albo wyczerpały
    się próby. Decyzję podejmuje **serwer**, a fragment po prostu przestaje
    nieść atrybuty HTMX — dzięki temu nie ma licznika w JavaScripcie, który
    trzeba by utrzymywać zgodny z tą regułą.
    """
    if od is None or proba >= MAKS_PROB_ODSWIEZENIA:
        return None
    if dane.last_seen_at is not None and dane.last_seen_at > od:
        return None
    return {
        "od": od.isoformat(),
        "proba": proba + 1,
        "opoznienie_s": OPOZNIENIE_PROBY_S,
    }


async def _popros_o_odswiezenie(kontekst: Any, dane: Any, teraz: dt.datetime) -> bool:
    """Ustawia natychmiastowy termin odpytu i budzi pętlę (SPEC.md §11.2).

    Nie wysyła żadnego żądania do serwisu — o tym, czy i kiedy ono poleci,
    decyduje jak zawsze dyspozytor razem z kubełkiem tokenów. Karta może
    najwyżej **poprosić**, i tylko o aukcję, która trwa i której dawno nie
    widzieliśmy: zakończonej nie ma po co odpytywać, a świeżej nie ma po co
    ponawiać przy każdym odświeżeniu przeglądarki.
    """
    if dane.pozycja.status is not AuctionStatus.ACTIVE:
        return False
    if (
        dane.last_seen_at is not None
        and (teraz - dane.last_seen_at).total_seconds() < ODSWIEZ_PO_S
    ):
        return False
    async with kontekst.uow as uow:
        await uow.auction.zaplanuj(dane.pozycja.id, teraz, dane.poll_tier)
    return True


@router.get(
    "/aukcja/{auction_id}/karta",
    response_class=HTMLResponse,
    name="szczegoly_fragment",
)
async def szczegoly_fragment(request: Request, auction_id: int) -> Response:
    """Sama karta aukcji — do podmiany w miejscu po odświeżeniu danych."""
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
    od = _czas_z_ciasteczka(request.query_params.get("od", ""))
    try:
        proba = int(request.query_params.get("proba", "0"))
    except ValueError:
        proba = MAKS_PROB_ODSWIEZENIA
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/karta-aukcji.html",
        context={
            **_kontekst_bazowy(request),
            "dane": dane,
            "odswiezanie": _stan_odswiezania(dane, od, proba),
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
        wystawienia = await kontekst.zapytania.powiazane_wystawienia(auction_id)
        przebieg = await kontekst.zapytania.przebieg_licytacji(
            auction_id, dane.semantyka_licznika
        )
        # Wejscie na karte = prosba o swiezy odczyt (§11.2). Prosba, nie
        # zadanie do serwisu: poleci dopiero z dyspozytora i tylko wtedy,
        # gdy pozwoli na to kubelek tokenow.
        teraz = dt.datetime.now(dt.UTC)
        poproszono = await _popros_o_odswiezenie(kontekst, dane, teraz)
    if poproszono:
        _budzik(request)

    return SZABLONY.TemplateResponse(
        request=request,
        name="szczegoly.html",
        context={
            **_kontekst_bazowy(request),
            "dane": dane,
            "wystawienia": wystawienia,
            "przebieg": przebieg,
            "odswiezanie": (
                _stan_odswiezania(dane, dane.last_seen_at or teraz, 0)
                if poproszono
                else None
            ),
        },
    )


def _kontrolki_obserwacji(request: Request, dane: Any) -> Response:
    if dane is None:
        return SZABLONY.TemplateResponse(
            request=request,
            name="nie-znaleziono.html",
            context={**_kontekst_bazowy(request), "auction_id": None},
            status_code=404,
        )
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/obserwowanie-szczegoly.html",
        context={**_kontekst_bazowy(request), "dane": dane},
    )


@router.post("/aukcja/{auction_id}/notatka", response_class=HTMLResponse)
async def zapisz_notatke(
    request: Request, auction_id: int, notatka: str = Form(default="")
) -> Response:
    """Zapisuje krótką notatkę wyłącznie dla obserwowanej aukcji."""
    if _fabryka(request) is None:
        return _brak_bazy(request)

    async with _fabryka(request)() as kontekst:
        async with kontekst.uow as uow:
            await uow.watchlist.zapisz_notatke(auction_id, notatka.strip() or None)
        dane = await kontekst.zapytania.szczegoly(auction_id)
    return _kontrolki_obserwacji(request, dane)


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

    adresy = await galeria.adresy(
        dane.pozycja.source_key, dane.pozycja.external_id, dane.pozycja.url
    )
    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/zdjecia.html",
        context={
            **_kontekst_bazowy(request),
            "auction_id": auction_id,
            "ile": len(adresy),
        },
    )


@router.get("/aukcja/{auction_id}/wycena", response_class=HTMLResponse)
async def wycena_aukcji(request: Request, auction_id: int) -> Response:
    """Pokazuje zapisaną wycenę; liczy tylko wtedy, gdy jeszcze jej nie ma."""
    return await _wycena(request, auction_id, przelicz=False)


@router.post("/aukcja/{auction_id}/wycena", response_class=HTMLResponse)
async def przelicz_wycene(request: Request, auction_id: int) -> Response:
    """Świadome przeliczenie na nowo — jedyny sposób na zmianę wyceny.

    Osobne żądanie i osobny przycisk, bo każde przeliczenie kosztuje
    u dostawcy i daje inną kwotę. Samo wejście na kartę nie ma prawa tego
    uruchamiać.
    """
    return await _wycena(request, auction_id, przelicz=True)


async def _wycena(request: Request, auction_id: int, *, przelicz: bool) -> Response:
    usluga = getattr(request.app.state, "wycena_ai", None)
    kontekst_szablonu: dict[str, Any] = {
        **_kontekst_bazowy(request),
        "auction_id": auction_id,
        "wycena": None,
        "blad": None,
        "brak_konfiguracji": False,
        # Przeliczyć da się tylko z działającym dostawcą; POKAZAĆ zapisaną
        # wycenę można zawsze.
        "mozna_przeliczyc": usluga is not None,
    }
    if _fabryka(request) is None:
        kontekst_szablonu["blad"] = "Baza danych jest teraz niedostępna."
        kontekst_szablonu["brak_konfiguracji"] = usluga is None
        return SZABLONY.TemplateResponse(
            request=request,
            name="fragmenty/wycena.html",
            context=kontekst_szablonu,
        )

    async with _fabryka(request)() as kontekst:
        async with kontekst.uow as uow:
            zapisana = await uow.wycena.dla_aukcji(auction_id)
        # Zapisana wycena wystarcza: to jest opinia o konkretnym aucie,
        # a nie odczyt, który trzeba odświeżać. Pokazujemy ją także wtedy,
        # gdy dostawca jest wyłączony — raz policzona wycena nie przestaje
        # być prawdziwa dlatego, że ktoś usunął klucz API.
        if zapisana is not None and not (przelicz and usluga is not None):
            kontekst_szablonu["wycena"] = zapisana
            return SZABLONY.TemplateResponse(
                request=request,
                name="fragmenty/wycena.html",
                context=kontekst_szablonu,
            )
        if usluga is None:
            kontekst_szablonu["brak_konfiguracji"] = True
            return SZABLONY.TemplateResponse(
                request=request,
                name="fragmenty/wycena.html",
                context=kontekst_szablonu,
            )

        dane = await kontekst.zapytania.szczegoly(auction_id)
        porownania = await kontekst.zapytania.porownania_rynkowe(auction_id)
        if dane is None:
            return HTMLResponse("", status_code=404)
        try:
            nowa = await usluga.wycen(dane, porownania, teraz=dt.datetime.now(dt.UTC))
        except BladWyceny as exc:
            kontekst_szablonu["blad"] = str(exc)
            # Nieudane przeliczenie nie ma kasować tego, co już mamy.
            kontekst_szablonu["wycena"] = zapisana
        else:
            async with kontekst.uow as uow:
                kontekst_szablonu["wycena"] = await uow.wycena.zapisz(nowa)

    return SZABLONY.TemplateResponse(
        request=request,
        name="fragmenty/wycena.html",
        context=kontekst_szablonu,
    )


@router.get("/aukcja/{auction_id}/zdjecie/{indeks}")
async def zdjecie(
    request: Request, auction_id: int, indeks: int, miniatura: bool = False
) -> Response:
    """Pojedyncze zdjęcie z cache'u na dysku (SPEC.md §12 — nie hotlink).

    Trasa przyjmuje **indeks**, nie adres. Gdyby przyjmowała adres, add-on
    byłby otwartym proxy: każdy, kto dosięgnie panelu, mógłby przez niego
    odpytywać dowolne adresy w sieci lokalnej.
    """
    galeria = getattr(request.app.state, "galeria", None)
    if galeria is None or _fabryka(request) is None:
        return _brak_zdjecia() if miniatura else Response(status_code=404)

    async with _fabryka(request)() as kontekst:
        dane = await kontekst.zapytania.szczegoly(auction_id)
    if dane is None:
        return _brak_zdjecia() if miniatura else Response(status_code=404)

    wynik = await galeria.obraz(
        dane.pozycja.source_key,
        dane.pozycja.external_id,
        indeks,
        miniatura=miniatura,
        url=dane.pozycja.url,
    )
    if wynik is None:
        return _brak_zdjecia() if miniatura else Response(status_code=404)
    tresc, typ = wynik
    # Zdjęcie aukcji nie zmienia się w trakcie jej trwania, a add-on i tak
    # trzyma je na dysku — niech przeglądarka nie pyta o nie przy każdym
    # otwarciu karty.
    return Response(tresc, media_type=typ, headers={"Cache-Control": "max-age=86400"})


def _brak_zdjecia() -> Response:
    """Neutralne wypełnienie kadru zamiast ikony uszkodzonego obrazu.

    Znak jest **mały i cichy**, a nie na całą kafelkę: w siatce kilkudziesięciu
    ofert brak zdjęcia to informacja poboczna, a duża ikona krzyczałaby
    głośniej niż cena i termin, czyli rzeczy, po które się tu przychodzi.
    Stąd sylwetka auta w środku pustego kadru, w kolorze ledwie odcinającym
    się od tła.

    SVG jest osobnym dokumentem, więc ma własne `prefers-color-scheme` —
    inaczej jasna plama świeciłaby w ciemnym panelu Home Assistanta.
    """
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 160">
<style>
  .tlo { fill: #f7f8fb }
  .znak { fill: #c3c9d8 }
  @media (prefers-color-scheme: dark) {
    .tlo { fill: #1b2134 }
    .znak { fill: #39415c }
  }
</style>
<rect class="tlo" width="240" height="160"/>
<g class="znak" transform="translate(120 80)">
  <path d="M-30-6 -24-17q1-3 4-3h40q3 0 4 3l6 11h3q3 0 3 3v11q0 3-3 3h-4
           a8 8 0 0 0-16 0h-20a8 8 0 0 0-16 0h-4q-3 0-3-3V-3q0-3 3-3z"/>
  <circle cx="-18" cy="5" r="5"/>
  <circle cx="18" cy="5" r="5"/>
</g>
</svg>"""
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=900"},
    )


@router.post("/aukcja/{auction_id}/przelacz", response_class=HTMLResponse)
async def przelacz_obserwacje(
    request: Request, auction_id: int, wariant: str = "lista"
) -> Response:
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
    if wariant == "szczegoly":
        return _kontrolki_obserwacji(request, dane)
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
                    criteria=_kryteria_do_zapisu(kryteria),
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
    # Stan kopii czytamy z KATALOGU, nie ze znacznika w bazie: plik nie
    # może się rozjechać z rzeczywistością, a znacznik owszem (§7.1).
    kopia = getattr(request.app.state, "kopia", None)
    ostatnia_kopia = kopia.ostatnia() if kopia is not None else None

    dane = Diagnostyka(
        polaczenie=stan.opcje.bezpieczny_opis(),
        baza_dostepna=stan.baza_dostepna,
        ostatni_blad_bazy=stan.ostatni_blad_bazy,
        rss_bajty=rss_bajty(),
        debug_dumps=stan.opcje.debug_dumps,
        ostatni_pg_dump=None if ostatnia_kopia is None else ostatnia_kopia.utworzono,
        kopia_bajty=None if ostatnia_kopia is None else ostatnia_kopia.bajtow,
        ostatni_blad_kopii=None if kopia is None else kopia.ostatni_blad,
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
            ostatni_pg_dump=dane.ostatni_pg_dump,
            kopia_bajty=dane.kopia_bajty,
            ostatni_blad_kopii=dane.ostatni_blad_kopii,
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
