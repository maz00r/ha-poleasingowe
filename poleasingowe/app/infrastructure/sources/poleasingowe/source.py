"""Adapter poleasingowe.pl — jedyne miejsce w tym pakiecie, które dotyka sieci.

Serwis czyta się **bez logowania**: komplet danych, razem z ceną, liczbą ofert
i absolutną datą końca, stoi serwerowo w HTML (RECON.md §4.2). Dlatego adapter
implementuje samo `AuctionSource`, a nie `AuthenticatedSource` — §10.1 zabrania
zmuszania adapterów serwisów publicznych do pustego `login()`.
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
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.poleasingowe import mapper, parser

KLUCZ = "poleasingowe"

# RECON.md §4.2: lista bez parametrów jest już posortowana po najbliższym
# końcu (`dzr`), co jest wprost tym, czego potrzebuje dispatcher.
SORTOWANIE_PO_KONCU = "dzr"

MAKS_STRON = 100
"""Bezpiecznik pętli przemiatu. ~73 strony przy 721 pojazdach (RECON.md §2.1)."""


def hash_tresci(bajty: bytes) -> str:
    """SPEC.md §11.3 krok 2 — identyczny hash oznacza pominięcie parsowania.

    Hashujemy **blok aukcji, nie całą stronę**. Hash całej strony jest tu
    bezużyteczny: trzy kolejne żądania dają trzy różne treści, bo zmienia się
    token CSRF i karuzela poleceń (zmierzone 2026-09-08, `parser`). Hash bloku
    aukcji był w tych samych żądaniach identyczny.

    Gdy bloku nie ma, wracamy do hasha całości — taka strona i tak nie da się
    sparsować, więc niestabilny hash niczego nie psuje.
    """
    tresc = bajty.decode("utf-8", "replace")
    blok = parser.wytnij_blok_aukcji(tresc)
    return hashlib.sha256((blok or tresc).encode("utf-8")).hexdigest()


class PoleasingoweSource:
    """Adapter serwisu publicznego — implementuje samo `AuctionSource`."""

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> PoleasingoweSource:
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

    async def __aenter__(self) -> PoleasingoweSource:
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
            raise SourceUnavailable(f"poleasingowe: {sciezka}: {exc}") from exc
        return odpowiedz.content

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        """Przemiata listę pojazdów, stronę po stronie.

        Aukcje nieobserwowane nie są odpytywane pojedynczo w ogóle — wystarcza
        im ten przemiat (SPEC.md §11.2).

        Warunkiem końca jest **brak nowych pozycji**, nie numer ostatniej
        strony z paginacji: serwis pokazuje w niej okno wokół bieżącej strony,
        więc „ostatnia" widoczna zmienia się w trakcie chodzenia po liście.
        """
        wszystkie: list[SurowaOferta] = []
        widziane: set[str] = set()
        for kategoria, sciezka in parser.SCIEZKI_LIST:
            for strona in range(1, MAKS_STRON + 1):
                tresc = await self._pobierz(
                    sciezka, {"page": strona, "sort": SORTOWANIE_PO_KONCU}
                )
                pozycje = parser.sparsuj_liste(tresc.decode("utf-8", "replace"))
                nowe = [p for p in pozycje if p.external_id not in widziane]
                if not nowe:
                    break
                widziane.update(p.external_id for p in nowe)
                # Kategoria listy jest jedyną deklaracją rodzaju, jaką ten
                # serwis daje — na samej stronie aukcji jej nie ma.
                wszystkie.extend(
                    replace(p, pola={**p.pola, "kategoria": kategoria}) for p in nowe
                )
        return wszystkie

    async def pobierz_szczegoly(
        self, external_id: str, znany_hash: str | None = None
    ) -> SurowaOferta | None:
        """Szczegóły jednej aukcji. Wywoływane tylko dla obserwowanych (§11.2).

        Hash liczymy z surowych bajtów **przed** parsowaniem — inaczej cała
        oszczędność z §11.3 znika.

        Slug w adresie jest dowolny — **sprawdzone**: serwis routuje po samym
        identyfikatorze i `/details/x/<id>` zwraca tę samą stronę co pełny
        slug. Stały slug jest przy okazji lepszy dla `content_hash`, bo slug
        pojawia się w treści strony.
        """
        sciezka = f"/pl/auctions/details/x/{external_id}"
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
        sciezka = strona_aukcji(
            url,
            bazowy=parser.BAZOWY_URL,
            zapasowa=f"/pl/auctions/details/x/{external_id}",
        )
        return parser.zdjecia((await self._pobierz(sciezka)).decode("utf-8", "replace"))

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
