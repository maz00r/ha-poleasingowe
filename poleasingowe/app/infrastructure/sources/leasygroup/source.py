"""Adapter anonimowych licytacji aukcje.leasygroup.pl."""

from __future__ import annotations

import datetime as dt
import hashlib
import ssl
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import TracebackType

import httpx

from app.application.ports import StronaPrzemiatu, SurowaOferta
from app.domain.entities import Auction
from app.domain.errors import ParseFailed, SourceUnavailable
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.leasygroup import mapper, parser

KLUCZ = "leasygroup"
MAKS_STRON = 100


def hash_tresci(bajty: bytes) -> str:
    """Odcisk odpowiedzi przed mapowaniem; licznik jest celowo częścią danych."""
    return hashlib.sha256(bajty).hexdigest()


class LeasygroupSource:
    """Publiczny adapter bez logowania i bez operacji licytowania."""

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> LeasygroupSource:
        return cls(
            httpx.AsyncClient(
                base_url=parser.BAZOWY_URL,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                headers={"Accept-Language": "pl-PL,pl;q=0.9"},
                # Serwis nie wysyła certyfikatu pośredniego. Obraz dodatku
                # dodaje go do systemowego magazynu zaufanych CA; jawny
                # kontekst systemowy zapewnia, że httpx go użyje zamiast
                # własnego pakietu certifi. Weryfikacja hosta pozostaje
                # domyślnie włączona.
                verify=ssl.create_default_context(),
            )
        )

    async def __aenter__(self) -> LeasygroupSource:
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

    async def _pobierz(self, sciezka: str) -> bytes:
        try:
            odpowiedz = await self._klient.get(sciezka)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Leasygroup: {sciezka}: {exc}") from exc
        return odpowiedz.content

    async def _pobierz_liste(self, sciezka: str) -> bytes:
        """Pobiera listę i rozpoznaje błędny status zwracany przez serwis.

        Leasygroup zdarza się zwracać pełną, poprawną listę aukcji z HTTP 404.
        Nie możemy bezwarunkowo uznać 404 za sukces — WAF lub prawdziwa strona
        błędu wciąż nie może potwierdzić skanu. Akceptujemy ją wyłącznie po
        rozpoznaniu własnego kontenera listy przez parser.
        """
        try:
            odpowiedz = await self._klient.get(sciezka)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Leasygroup: {sciezka}: {exc}") from exc

        if odpowiedz.is_success:
            return odpowiedz.content
        if odpowiedz.status_code == httpx.codes.NOT_FOUND:
            tresc = odpowiedz.content.decode("utf-8", "replace")
            try:
                parser.identyfikatory_wierszy(tresc)
            except ParseFailed as exc:
                raise ParseFailed(
                    f"Leasygroup: HTTP 404 bez poprawnej listy: {sciezka}"
                ) from exc
            return odpowiedz.content
        raise SourceUnavailable(f"Leasygroup: {sciezka}: HTTP {odpowiedz.status_code}")

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        wynik: list[SurowaOferta] = []
        async for strona in self.strony_przemiatu():
            wynik.extend(strona.pozycje)
        return wynik

    async def strony_przemiatu(self) -> AsyncIterator[StronaPrzemiatu]:
        """Czyta każdą stronę zadeklarowaną przez paginator pierwszej strony."""
        widziane_wiersze: set[str] = set()
        ostatnia_strona: int | None = None
        for numer in range(1, MAKS_STRON + 1):
            sciezka = parser.SCIEZKA_LISTY.format(numer)
            tresc = await self._pobierz_liste(sciezka)
            html = tresc.decode("utf-8", "replace")
            wszystkie = parser.identyfikatory_wierszy(html)
            czy_powtorzona = wszystkie and any(
                identyfikator in widziane_wiersze for identyfikator in wszystkie
            )
            if czy_powtorzona:
                raise ParseFailed(f"Leasygroup: zapętlona paginacja na stronie {numer}")
            widziane_wiersze.update(wszystkie)

            if ostatnia_strona is None:
                strony = parser.numery_stron(html)
                ostatnia_strona = max(strony, default=1)
                if ostatnia_strona > MAKS_STRON:
                    raise ParseFailed(
                        f"Leasygroup: paginator przekracza limit {MAKS_STRON} stron"
                    )

            yield StronaPrzemiatu(tuple(parser.sparsuj_liste(html)))
            if numer >= ostatnia_strona:
                return
        raise ParseFailed(f"Leasygroup: osiągnięto limit {MAKS_STRON} stron")

    async def pobierz_szczegoly(
        self,
        external_id: str,
        znany_hash: str | None = None,
        url: str | None = None,
    ) -> SurowaOferta | None:
        """Szczegóły przez URL zapamiętany przy skanie; slug jest obowiązkowy."""
        sciezka = strona_aukcji(
            url,
            bazowy=parser.BAZOWY_URL,
            zapasowa=f"/aukcja/{external_id}/",
        )
        tresc = await self._pobierz(sciezka)
        biezacy = hash_tresci(tresc)
        if znany_hash is not None and znany_hash == biezacy:
            return None
        pelny_url = (
            sciezka if sciezka.startswith("http") else f"{parser.BAZOWY_URL}{sciezka}"
        )
        return replace(
            parser.sparsuj_szczegoly(
                tresc.decode("utf-8", "replace"), external_id, pelny_url
            ),
            content_hash=biezacy,
        )

    async def zdjecia(self, external_id: str, url: str | None = None) -> Sequence[str]:
        sciezka = strona_aukcji(
            url,
            bazowy=parser.BAZOWY_URL,
            zapasowa=f"/aukcja/{external_id}/",
        )
        return parser.zdjecia((await self._pobierz(sciezka)).decode("utf-8", "replace"))

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
