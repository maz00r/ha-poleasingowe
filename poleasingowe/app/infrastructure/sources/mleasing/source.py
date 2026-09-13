"""Adapter publicznych licytacji portalaukcyjny.mleasing.pl."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import TracebackType
from typing import Any

import httpx

from app.application.ports import StronaPrzemiatu, SurowaOferta
from app.domain.entities import Auction
from app.domain.errors import ParseFailed, SourceUnavailable
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.mleasing import mapper, parser

KLUCZ = "mleasing"
NA_STRONE = 15
MAKS_STRON = 100


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
        "auctionTypes": ["Auction"],
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

    async def _strona(self, kategoria: str, numer: int) -> bytes:
        try:
            odpowiedz = await self._klient.post(
                parser.SCIEZKA_SZUKANIA,
                json=_parametry_wyszukiwania(kategoria, numer),
            )
            odpowiedz.raise_for_status()
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
                wynik = parser.sparsuj_strone(
                    await self._strona(kategoria, numer), kategoria=kategoria
                )
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
