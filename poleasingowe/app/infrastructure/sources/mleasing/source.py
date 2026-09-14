"""Adapter publicznych licytacji portalaukcyjny.mleasing.pl."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from app.application.ports import StronaPrzemiatu, SurowaOferta
from app.domain.entities import Auction
from app.domain.errors import ParseFailed, SourceUnavailable
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.mleasing import mapper, parser

KLUCZ = "mleasing"
NA_STRONE = 15
MAKS_STRON = 100
_SCIEZKI_KATEGORII = {
    "Passenger": "/oferty/osobowe/",
    "Vans": "/oferty/dostawcze/",
}
_CIASTECZKO_XSRF = "XSRF-TOKEN"
_NAGLOWEK_XSRF = "X-XSRF-TOKEN"
_NAGLOWKI_PRZEGLADARKI = {
    "Accept": "application/json, text/plain, */*",
    "Origin": parser.BAZOWY_URL,
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}


def _parametry_wyszukiwania(kategoria: str, numer: int) -> dict[str, Any]:
    """Kontrakt generowany przez obecną aplikację Next.js mLeasing."""
    return {
        "pageNumber": numer,
        "pageSize": NA_STRONE,
        "category": kategoria,
        "makes": None,
        "models": None,
        "branchIds": None,
        "gearBoxTypes": None,
        "fuelTypes": None,
        "bodyTypes": None,
        "driveTypes": None,
        # Portal nie zawęża domyślnej listy parametrem `auctionTypes`.
        # Bierzemy jego pełny wynik i odrzucamy ogłoszenia po stronie parsera.
        # Dzięki temu payload pozostaje zgodny z tym z widoku `/oferty/*`.
        "auctionTypes": None,
        "yearFrom": None,
        "amountTo": None,
        "mileageFrom": None,
        "mileageTo": None,
        "engineCapacityFrom": None,
        "engineCapacityTo": None,
        "enginePowerHpFrom": None,
        "enginePowerHpTo": None,
        "numberOfSeats": None,
        "orderBy": [
            {"fieldName": "isDamaged", "descending": False},
            {"fieldName": "auctionType", "descending": False},
            {"fieldName": "isPromoted", "descending": True},
            {"fieldName": "id", "descending": True},
        ],
        "searchText": "",
    }


class MleasingSource:
    """Odczyt publiczny, bez logowania i bez możliwości licytowania."""

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient
        self._sesje_wyszukiwania: set[str] = set()
        self._tokeny_xsrf: dict[str, str] = {}

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> MleasingSource:
        return cls(
            httpx.AsyncClient(
                base_url=parser.BAZOWY_URL,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                headers={"Accept-Language": "pl-PL,pl;q=0.9"},
            )
        )

    async def __aenter__(self) -> MleasingSource:
        return self

    async def __aexit__(
        self,
        typ: type[BaseException] | None,
        wyjatek: BaseException | None,
        slad: TracebackType | None,
    ) -> None:
        await self.zamknij()

    async def zamknij(self) -> None:
        await self._klient.aclose()

    async def _get(
        self, sciezka: str, *, params: dict[str, str] | None = None
    ) -> bytes:
        try:
            odpowiedz = await self._klient.get(sciezka, params=params)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"mLeasing: {sciezka}: {exc}") from exc
        return odpowiedz.content

    async def _przygotuj_wyszukiwanie(self, kategoria: str, *, odswiez: bool) -> None:
        """Zakłada publiczną sesję wymaganą przez wyszukiwarkę portalu.

        Aplikacja Next.js najpierw otwiera stronę kategorii, a później wysyła
        cookie `XSRF-TOKEN` jako nagłówek przy POST. Bez tego kroku endpoint
        zwraca HTTP 400 mimo że lista jest widoczna w przeglądarce.
        """
        if kategoria in self._sesje_wyszukiwania and not odswiez:
            return
        sciezka = _SCIEZKI_KATEGORII.get(kategoria)
        if sciezka is None:
            raise ParseFailed(f"mLeasing: nieznana kategoria {kategoria!r}")
        try:
            odpowiedz = await self._klient.get(sciezka)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"mLeasing: nie udało się otworzyć kategorii {kategoria}: {exc}"
            ) from exc
        token = self._token_xsrf_dla_sciezki(sciezka)
        if token is None:
            self._tokeny_xsrf.pop(kategoria, None)
        else:
            self._tokeny_xsrf[kategoria] = token
        self._sesje_wyszukiwania.add(kategoria)

    def _token_xsrf_dla_sciezki(self, sciezka: str) -> str | None:
        """Zwraca najwężej dopasowany token widoczny na stronie kategorii.

        mLeasing może nadać osobne cookie `XSRF-TOKEN` dla `/oferty/osobowe/`
        i `/oferty/dostawcze/`. `httpx.Cookies.get()` zgłasza wtedy konflikt;
        przeglądarka wybiera cookie o najdłuższej pasującej ścieżce i my robimy
        to samo, zanim przekażemy token w nagłówku axiosa.
        """
        host = urlsplit(parser.BAZOWY_URL).hostname
        pasujace = []
        for cookie in self._klient.cookies.jar:
            if cookie.name != _CIASTECZKO_XSRF:
                continue
            domena = cookie.domain.lstrip(".")
            if host is not None and domena not in {"", host}:
                continue
            sciezka_cookie = cookie.path or "/"
            if sciezka.startswith(sciezka_cookie.rstrip("/") + "/") or sciezka == (
                sciezka_cookie.rstrip("/") or "/"
            ):
                pasujace.append(cookie)
        if not pasujace:
            return None
        token = max(pasujace, key=lambda cookie: len(cookie.path or "/")).value
        if token is None:
            return None
        return unquote(token)

    def _naglowki_wyszukiwania(self, kategoria: str) -> dict[str, str]:
        """Odtwarza kontekst żądania wysyłanego przez widok kategorii."""
        sciezka = _SCIEZKI_KATEGORII[kategoria]
        naglowki = {
            **_NAGLOWKI_PRZEGLADARKI,
            "Referer": f"{parser.BAZOWY_URL}{sciezka}",
        }
        if (token := self._tokeny_xsrf.get(kategoria)) is not None:
            naglowki[_NAGLOWEK_XSRF] = token
        return naglowki

    async def _wyslij_wyszukiwanie(self, kategoria: str, numer: int) -> httpx.Response:
        return await self._klient.post(
            parser.SCIEZKA_SZUKANIA,
            json=_parametry_wyszukiwania(kategoria, numer),
            headers=self._naglowki_wyszukiwania(kategoria),
        )

    async def _strona(self, kategoria: str, numer: int) -> bytes:
        try:
            await self._przygotuj_wyszukiwanie(kategoria, odswiez=False)
            odpowiedz = await self._wyslij_wyszukiwanie(kategoria, numer)
            # Token sesji może wygasnąć między kolejnymi przemiotami. Jedna
            # próba po odświeżeniu odwzorowuje zwykłe ponowne wejście na listę.
            # Trwałe 400 przerywa skan: ogólne kolekcje awaryjne nie niosą
            # kategorii, więc nie wolno nimi zastępować listy samochodów.
            if odpowiedz.status_code == httpx.codes.BAD_REQUEST:
                await self._przygotuj_wyszukiwanie(kategoria, odswiez=True)
                odpowiedz = await self._wyslij_wyszukiwanie(kategoria, numer)
            odpowiedz.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.BAD_REQUEST:
                raise SourceUnavailable(
                    f"mLeasing: wyszukiwanie {kategoria}, strona {numer}: HTTP 400"
                ) from exc
            raise SourceUnavailable(
                f"mLeasing: wyszukiwanie {kategoria}, strona {numer}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"mLeasing: wyszukiwanie {kategoria}, strona {numer}: {exc}"
            ) from exc
        return odpowiedz.content

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        wynik: list[SurowaOferta] = []
        async for strona in self.strony_przemiatu():
            wynik.extend(strona.pozycje)
        return wynik

    async def strony_przemiatu(self) -> AsyncIterator[StronaPrzemiatu]:
        widziane_globalnie: set[str] = set()
        for kategoria in parser.KATEGORIE:
            widziane: set[str] = set()
            oczekiwana_liczba: int | None = None
            oczekiwane_strony: int | None = None
            for numer in range(1, MAKS_STRON + 1):
                tresc = await self._strona(kategoria, numer)
                wynik = parser.sparsuj_strone(tresc, kategoria=kategoria)
                if oczekiwana_liczba is None:
                    oczekiwana_liczba = wynik.total_count
                    oczekiwane_strony = max(1, math.ceil(wynik.total_count / NA_STRONE))
                    if oczekiwane_strony > MAKS_STRON:
                        raise ParseFailed(
                            f"mLeasing: {kategoria} przekracza limit {MAKS_STRON} stron"
                        )
                elif wynik.total_count != oczekiwana_liczba:
                    raise ParseFailed(
                        f"mLeasing: liczba ofert {kategoria} "
                        "zmieniła się w trakcie skanu"
                    )

                if len(wynik.identyfikatory) != len(set(wynik.identyfikatory)):
                    raise ParseFailed(
                        f"mLeasing: duplikat na stronie {numer} ({kategoria})"
                    )
                powtorzone = set(wynik.identyfikatory) & widziane
                if powtorzone:
                    raise ParseFailed(
                        f"mLeasing: zapętlona paginacja na stronie {numer} "
                        f"({kategoria})"
                    )
                widziane.update(wynik.identyfikatory)

                nowe = tuple(
                    p for p in wynik.pozycje if p.external_id not in widziane_globalnie
                )
                widziane_globalnie.update(p.external_id for p in nowe)
                yield StronaPrzemiatu(nowe)

                assert oczekiwane_strony is not None
                if numer >= oczekiwane_strony:
                    if len(widziane) != oczekiwana_liczba:
                        raise ParseFailed(
                            f"mLeasing: niepełna paginacja {kategoria}: "
                            f"{len(widziane)}/{oczekiwana_liczba}"
                        )
                    break
            else:
                raise ParseFailed(f"mLeasing: osiągnięto limit {MAKS_STRON} stron")

    async def pobierz_szczegoly(
        self,
        external_id: str,
        znany_hash: str | None = None,
        url: str | None = None,
    ) -> SurowaOferta | None:
        if not external_id.isdigit():
            raise ParseFailed("mLeasing: identyfikator aukcji nie jest liczbą")
        zapasowa = f"/oferta/{external_id}/"
        strona = strona_aukcji(url, bazowy=parser.BAZOWY_URL, zapasowa=zapasowa)
        pelny_url = (
            strona if strona.startswith("http") else f"{parser.BAZOWY_URL}{strona}"
        )
        szczegoly = await self._get("/api/offer-read/get", params={"id": external_id})
        lokalizacje = await self._get(
            "/api/offer-read/get-locations", params={"id": external_id}
        )
        biezacy = hashlib.sha256(szczegoly + b"\0" + lokalizacje).hexdigest()
        if znany_hash is not None and znany_hash == biezacy:
            return None
        return replace(
            parser.sparsuj_szczegoly(
                szczegoly,
                lokalizacje,
                external_id=external_id,
                url=pelny_url,
            ),
            content_hash=biezacy,
        )

    async def zdjecia(self, external_id: str, url: str | None = None) -> Sequence[str]:
        if not external_id.isdigit():
            raise ParseFailed("mLeasing: identyfikator aukcji nie jest liczbą")
        # Walidacja zapamiętanego URL blokuje użycie galerii jako proxy do
        # obcego hosta. Sam endpoint zdjęć pozostaje stały.
        strona_aukcji(
            url,
            bazowy=parser.BAZOWY_URL,
            zapasowa=f"/oferta/{external_id}/",
        )
        return parser.zdjecia(
            await self._get(
                "/api/offer-read/get-images", params={"offerId": external_id}
            )
        )

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
