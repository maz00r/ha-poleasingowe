"""Adapter autoprzetarg.pl — jedyne miejsce w tym pakiecie dotykające sieci.

Odczyt działa **bez logowania**: lista niesie komplet danych technicznych
razem z VIN-em i absolutnym terminem (RECON.md §4.4). Sesja dokłada wyłącznie
**liczbę i historię ofert**, więc adapter implementuje na razie samo
`AuctionSource` — §10.1 zabrania pustych implementacji „na wyrost".

Do huba SignalR (`auctionHub`) **nie podchodzimy**. W tym samym hubie siedzą
`revertLastOffer` i `revertLastOfferFromAdmin`, czyli operacje mutujące,
a aplikacja jest wyłącznie do odczytu.
"""

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
from app.infrastructure.sources.autoprzetarg import mapper, parser

KLUCZ = "autoprzetarg"

MAKS_STRON = 40
"""Bezpiecznik pętli. 244 oferty po 12 na stronę to ~21 stron (RECON.md §2.1)."""


def hash_tresci(bajty: bytes) -> str:
    """SPEC.md §11.3 krok 2 — identyczny hash oznacza pominięcie parsowania.

    Serwis nie zwraca `ETag` ani `Last-Modified`, a `If-Modified-Since`
    dostaje 200 (RECON.md §3.1), więc to jedyny działający tani odpyt.

    Hashujemy **odcisk pól aukcji**, nie surową odpowiedź: strona ma cztery
    niezależne fragmenty zmieniane przy każdym żądaniu, żaden niezwiązany
    z aukcją. Szczegóły i pomiar — w `parser.odcisk_aukcji`.
    """
    odcisk = parser.odcisk_aukcji(bajty.decode("utf-8", "replace"))
    return hashlib.sha256(odcisk.encode("utf-8")).hexdigest()


class AutoprzetargSource:
    """Adapter serwisu — na razie wyłącznie odczyt anonimowy."""

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> AutoprzetargSource:
        """Jeden `AsyncClient` per źródło, tworzony raz (SPEC.md §11.3)."""
        return cls(
            httpx.AsyncClient(
                base_url=parser.BAZOWY_URL,
                http2=True,
                timeout=timeout,
                # `follow_redirects=False`: przekierowanie NIE jest tu
                # szczegółem transportu, tylko informacją. Aukcja po terminie
                # przestaje istnieć pod swoim adresem i serwis odsyła na stronę
                # główną (RECON.md §3.4) — to jedyny marker jej zakończenia.
                follow_redirects=False,
                # Accept-Encoding ustawia httpx, a NIE my. Recznie wpisany
                # `gzip, br` oglaszal brotli, ktorego klient nie umial
                # rozpakowac — serwer wybieral `br` i dostawalismy bajty
                # nie do odczytania. httpx oglasza dokladnie to, co ma
                # dekoder, wiec rozjazd staje sie niemozliwy (SPEC.md §11.3).
                headers={"Accept-Language": "pl-PL,pl;q=0.9"},
            )
        )

    async def __aenter__(self) -> AutoprzetargSource:
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
    ) -> httpx.Response:
        try:
            odpowiedz = await self._klient.get(sciezka, params=parametry)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"autoprzetarg: {sciezka}: {exc}") from exc
        if odpowiedz.status_code >= 400:
            raise SourceUnavailable(
                f"autoprzetarg: {sciezka}: HTTP {odpowiedz.status_code}"
            )
        return odpowiedz

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        """Przemiata listę pojazdów, stronę po stronie (SPEC.md §11.2)."""
        wszystkie: list[SurowaOferta] = []
        widziane: set[str] = set()
        for strona in range(1, MAKS_STRON + 1):
            odpowiedz = await self._pobierz(parser.SCIEZKA_LISTY, {"page": strona})
            pozycje = parser.sparsuj_liste(odpowiedz.text)
            nowe = [p for p in pozycje if p.external_id not in widziane]
            if not nowe:
                break
            widziane.update(p.external_id for p in nowe)
            wszystkie.extend(nowe)
        return wszystkie

    async def pobierz_szczegoly(
        self, external_id: str, znany_hash: str | None = None
    ) -> SurowaOferta | None:
        """Szczegóły jednej aukcji (SPEC.md §11.2).

        Przekierowanie znaczy „aukcja się skończyła i zniknęła", a nie „błąd".
        Zwracamy wtedy pozycję ze znacznikiem, żeby warstwa wyżej mogła
        zamknąć aukcję zamiast liczyć to jako awarię źródła (§11.5).
        """
        sciezka = f"/aukcja/x,{external_id},x"
        odpowiedz = await self._pobierz(sciezka)

        if parser.czy_zakonczona(
            odpowiedz.status_code, str(odpowiedz.headers.get("location", ""))
        ):
            return SurowaOferta(
                external_id=external_id,
                url=f"{parser.BAZOWY_URL}{sciezka}",
                pola={"zniknela": "1"},
            )

        biezacy = hash_tresci(odpowiedz.content)
        if znany_hash is not None and biezacy == znany_hash:
            return None

        surowa = parser.sparsuj_szczegoly(
            odpowiedz.text, external_id, f"{parser.BAZOWY_URL}{sciezka}"
        )
        return replace(surowa, content_hash=biezacy)

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
