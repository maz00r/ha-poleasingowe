"""Adapter EFL — jedyne miejsce w tym pakiecie, które dotyka sieci."""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import TracebackType

import httpx

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.errors import SourceUnavailable
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.efl import mapper, parser

KLUCZ = "efl"

# Kategoria pojazdow osobowych ustalona z fixtures (RECON.md §4.1).
KATEGORIA_OSOBOWE = 16
# perPage do 64 — jeden przemiat calej kategorii to wtedy kilkanascie zadan
# zamiast kilkudziesieciu (RECON.md §2.1).
NA_STRONE = 64


def hash_tresci(bajty: bytes) -> str:
    """SPEC.md §11.3 krok 2 — identyczny hash oznacza pominięcie parsowania.

    Parsowanie jest najdroższą operacją CPU w całej aplikacji i nie wolno go
    wykonywać na niezmienionej treści. Dla EFL to jedyny działający mechanizm
    taniego odpytu: serwis nie zwraca `ETag` ani `Last-Modified`, a
    `If-Modified-Since` dostaje 200, nie 304 (RECON.md §3.1).
    """
    return hashlib.sha256(bajty).hexdigest()


class EflSource:
    """Adapter serwisu publicznego — implementuje samo `AuctionSource`.

    Nie ma `login()`, bo EFL nie wymaga logowania do odczytu, a SPEC.md §10.1
    zabrania zmuszania takich adapterów do pustej implementacji.
    """

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> EflSource:
        """Jeden `AsyncClient` per źródło, tworzony raz (SPEC.md §11.3)."""
        return cls(
            httpx.AsyncClient(
                base_url=parser.BAZOWY_URL,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                # Accept-Encoding ustawia httpx, a NIE my. Recznie wpisany
                # `gzip, br` oglaszal brotli, ktorego klient nie umial
                # rozpakowac — serwer wybieral `br` i dostawalismy bajty
                # nie do odczytania. httpx oglasza dokladnie to, co ma
                # dekoder, wiec rozjazd staje sie niemozliwy (SPEC.md §11.3).
                headers={"Accept-Language": "pl-PL,pl;q=0.9"},
            )
        )

    async def __aenter__(self) -> EflSource:
        return self

    async def __aexit__(
        self,
        typ: type[BaseException] | None,
        wyjatek: BaseException | None,
        slad: TracebackType | None,
    ) -> None:
        await self.zamknij()

    async def zamknij(self) -> None:
        """SPEC.md §7.1 — sesje httpx domykają się czysto na SIGTERM."""
        await self._klient.aclose()

    async def _pobierz(
        self, sciezka: str, parametry: Mapping[str, str | int] | None = None
    ) -> bytes:
        try:
            odpowiedz = await self._klient.get(sciezka, params=parametry)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"EFL: {sciezka}: {exc}") from exc
        return odpowiedz.content

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        """Przemiata całą kategorię, stronę po stronie.

        Aukcje nieobserwowane nie są odpytywane pojedynczo w ogóle — wystarcza
        im ten przemiat (SPEC.md §11.2). To główna oszczędność systemu.
        """
        wszystkie: list[SurowaOferta] = []
        widziane: set[str] = set()
        strona = 0
        while True:
            tresc = await self._pobierz(
                "/AuctionList",
                {
                    "category": KATEGORIA_OSOBOWE,
                    "sort": "EndDate-asc",
                    "perPage": NA_STRONE,
                    "page": strona,
                },
            )
            pozycje = parser.sparsuj_liste(tresc.decode("utf-8", "replace"))
            nowe = [p for p in pozycje if p.external_id not in widziane]
            if not nowe:
                break
            widziane.update(p.external_id for p in nowe)
            wszystkie.extend(nowe)
            strona += 1
        return wszystkie

    async def pobierz_szczegoly(
        self, external_id: str, znany_hash: str | None = None
    ) -> SurowaOferta | None:
        """Szczegóły jednej aukcji. Wywoływane tylko dla obserwowanych (§11.2).

        Hash liczymy z surowych bajtów **przed** parsowaniem — inaczej cała
        oszczędność z §11.3 znika, bo najdroższa operacja i tak by się
        wykonała.
        """
        sciezka = f"/Auction/x-id{external_id}"
        tresc = await self._pobierz(sciezka)
        biezacy = hash_tresci(tresc)
        if znany_hash is not None and biezacy == znany_hash:
            return None

        surowa = parser.sparsuj_szczegoly(
            tresc.decode("utf-8", "replace"),
            external_id,
            f"{parser.BAZOWY_URL}{sciezka}",
        )
        return replace(surowa, content_hash=biezacy)

    async def zdjecia(self, external_id: str, url: str | None = None) -> Sequence[str]:
        """Adresy zdjęć pojazdu (SPEC.md §12).

        Wywoływane **na żądanie**, gdy ktoś otworzy kartę aukcji — nie przy
        zbieraniu. Adresy nie trafiają do bazy: to jedno żądanie na obejrzaną
        aukcję zamiast kolumny utrzymywanej dla wszystkich.
        """
        # Adres z bazy przed skladanym: `x-id` to zalozenie o routingu
        # EFL, ktorego rekonesans NIE potwierdzil (`sources/adresy.py`).
        sciezka = strona_aukcji(
            url, bazowy=parser.BAZOWY_URL, zapasowa=f"/Auction/x-id{external_id}"
        )
        return parser.zdjecia((await self._pobierz(sciezka)).decode("utf-8", "replace"))

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
