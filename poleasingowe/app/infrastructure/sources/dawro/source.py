"""Adapter publicznych aukcji dawro.pl.

Odczyt działa bez logowania: lista i szczegóły niosą komplet danych
opisowych, VIN-u i terminu. Licytowanie jest za logowaniem
(`Dialog.logowanie()`), ale aplikacja nigdy nie licytuje, więc adapter
implementuje samo `AuctionSource` — SPEC.md §10.1 zabrania pustego
`zaloguj()` „na wyrost".

**Paginacja bez licznika stron.** W przeciwieństwie do Leasygroup (widget
`div.pagination_container`), dawro nie ma nigdzie numeru ostatniej strony.
Żądanie strony za katalogiem nie daje pustej odpowiedzi ani błędu —
przekierowuje na stronę 1 (zmierzone: `fixtures/dawro/meta.json`,
`lista-02.html` ma `redirects: 1` i wraca na `strona,1`). Detekcja końca
więc nie sprawdza samej odpowiedzi, tylko to, czy strona > 1 przyniosła
choć jeden NOWY `external_id` — powtórka jest tu prawidłowym końcem listy,
nie błędem (inaczej niż zapętlenie u Leasygroup/autoprzetarg, gdzie to
sygnał awarii, bo tam koniec rozpoznaje się inaczej).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import TracebackType

import httpx

from app.application.ports import StronaPrzemiatu, SurowaOferta
from app.domain.entities import Auction
from app.domain.errors import ParseFailed, SourceUnavailable
from app.infrastructure.sources.adresy import strona_aukcji
from app.infrastructure.sources.dawro import mapper, parser

KLUCZ = "dawro"

MAKS_STRON = 40
"""Bezpiecznik pętli. Katalog zmierzony na 24 pozycje / 1 strona (§4.5)."""

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
"""Ten sam User-Agent, którym wykonano CAŁY rekonesans i pomiar domknięcia
(`tools/pomiar_dawro.py`). Zachowanie serwisu wobec domyślnego
`python-httpx/…` nie jest zmierzone — narzędzie rekonesansu ma osobną
detekcję WAF, więc autor liczył się z blokadą. Nie zgadujemy: wysyłamy to,
co na pewno działało."""


def hash_tresci(tekst: str) -> str:
    """SPEC.md §11.3 krok 2 — odcisk pól aukcji, nie surowych bajtów.

    Strona ma widget rekomendacji na górze (przed blokiem aukcji), którego
    zawartość nie jest zmierzona jako stała między żądaniami — hashowanie
    całej odpowiedzi ryzykowałoby fałszywe „zmieniło się" przy każdym
    odpycie (ten sam problem, który autoprzetarg.pl rozwiązuje własnym
    `odcisk_aukcji`).
    """
    return hashlib.sha256(parser.odcisk_aukcji(tekst).encode("utf-8")).hexdigest()


class DawroSource:
    """Adapter serwisu — wyłącznie odczyt anonimowy."""

    key = KLUCZ

    def __init__(self, klient: httpx.AsyncClient) -> None:
        self._klient = klient

    @classmethod
    def utworz(cls, *, timeout: float = 30.0) -> DawroSource:
        """Jeden `AsyncClient` per źródło, tworzony raz (SPEC.md §11.3)."""
        return cls(
            httpx.AsyncClient(
                base_url=parser.BAZOWY_URL,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                headers={"Accept-Language": "pl-PL,pl;q=0.9", "User-Agent": _UA},
            )
        )

    async def __aenter__(self) -> DawroSource:
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

    async def _pobierz(self, sciezka: str) -> tuple[str, str]:
        """GET → (ścieżka KOŃCOWA, treść zdekodowana jako UTF-8).

        Treść: nagłówek deklaruje `iso-8859-1`, a jest UTF-8 (RECON.md §4.5)
        — `.text` httpx ufałby nagłówkowi.

        Ścieżka końcowa: klient podąża za przekierowaniami (`dawro.pl` →
        `www.`), więc przekierowanie NIE jest widoczne w kodzie odpowiedzi.
        A jest informacją: aukcja zakończona przed godzinami dostaje `302`
        na `/` (zmierzone na żywo 2026-09-14 ~21:00 dla `16761`, zakończonej
        o 10:00 — pomiar domknięcia sięgał tylko T+600 s, gdzie strona
        jeszcze stała). Strona główna ma `#tresc-strony` i kafelki
        `aukcja-box`, więc bez tej informacji uchodziłaby za listę albo
        rzucała `ParseFailed` liczonym jako awaria źródła.
        """
        try:
            odpowiedz = await self._klient.get(sciezka)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"dawro: {sciezka}: {exc}") from exc
        return odpowiedz.url.path, odpowiedz.content.decode("utf-8", "replace")

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        wynik: list[SurowaOferta] = []
        async for strona in self.strony_przemiatu():
            wynik.extend(strona.pozycje)
        return wynik

    async def strony_przemiatu(self) -> AsyncIterator[StronaPrzemiatu]:
        """Czyta strony, aż strona > 1 nie przyniesie żadnego nowego ID."""
        widziane: set[str] = set()
        for numer in range(1, MAKS_STRON + 1):
            sciezka_koncowa, html = await self._pobierz(
                parser.SCIEZKA_LISTY.format(numer)
            )
            if not sciezka_koncowa.startswith("/aukcje/"):
                # Przekierowanie POZA katalog (na `/` albo landing). Strona
                # główna też ma kafelki — ale 12 wyróżnionych, nie katalog —
                # więc wynik nie może udawać kompletnego przemiatu. Błąd
                # daje `FAILED`, który niczego nie oznacza jako zniknięte.
                raise ParseFailed(
                    f"dawro: lista przekierowana poza katalog: {sciezka_koncowa}"
                )
            pozycje = parser.sparsuj_liste(html)
            # Odsiew także W OBRĘBIE strony: dwa kafelki tej samej aukcji
            # w jednym `zapisz_z_przemiatu` to konflikt w jednym `INSERT`.
            nowe: list[SurowaOferta] = []
            for pozycja in pozycje:
                if pozycja.external_id not in widziane:
                    widziane.add(pozycja.external_id)
                    nowe.append(pozycja)
            if numer > 1 and not nowe:
                # Katalog się skończył — serwis przekierowuje za stronę 1,
                # więc dostajemy jej treść jeszcze raz, bez nowych ID.
                return
            yield StronaPrzemiatu(tuple(nowe))
            if not pozycje:
                return
        raise ParseFailed(f"dawro: osiągnięto limit {MAKS_STRON} stron")

    async def pobierz_szczegoly(
        self,
        external_id: str,
        znany_hash: str | None = None,
        url: str | None = None,
    ) -> SurowaOferta | None:
        """Szczegóły przez URL zapamiętany przy skanie (`/aukcja/<id>,<slug>`).

        Ścieżka zapasowa bez sluga jest **niezmierzona** — RECON.md §4.5 nie
        mówi, czy serwis routuje po samym `<id>`. W praktyce nie ma kiedy
        zadziałać: każdy wiersz dawro powstaje ze skanu i ma pełny adres.
        Zostaje jako ostatnia deska ratunku dla `adresy.strona_aukcji`,
        a nie jako założenie o serwisie.
        """
        sciezka = strona_aukcji(
            url, bazowy=parser.BAZOWY_URL, zapasowa=f"/aukcja/{external_id}"
        )
        pelny_url = (
            sciezka if sciezka.startswith("http") else f"{parser.BAZOWY_URL}{sciezka}"
        )
        sciezka_koncowa, tekst = await self._pobierz(sciezka)
        if not sciezka_koncowa.startswith("/aukcja/"):
            # `302` na `/` godziny po końcu — aukcja przestała istnieć pod
            # swoim adresem, jak w autoprzetarg.pl. To fakt zniknięcia, nie
            # awaria: ten sam znacznik co „AUKCJA ZAKOŃCZONA", ta sama
            # minimalna encja `DISAPPEARED` w mapperze.
            return SurowaOferta(
                external_id=external_id, url=pelny_url, pola={"zamknieta": "1"}
            )
        biezacy = hash_tresci(tekst)
        if znany_hash is not None and znany_hash == biezacy:
            return None
        return replace(
            parser.sparsuj_szczegoly(tekst, external_id, pelny_url),
            content_hash=biezacy,
        )

    async def zdjecia(self, external_id: str, url: str | None = None) -> Sequence[str]:
        sciezka = strona_aukcji(
            url, bazowy=parser.BAZOWY_URL, zapasowa=f"/aukcja/{external_id}"
        )
        sciezka_koncowa, tekst = await self._pobierz(sciezka)
        # Aukcja przekierowana na `/` nie ma już galerii — pusta lista, nie
        # zdjęcia z karuzeli strony głównej.
        if not sciezka_koncowa.startswith("/aukcja/"):
            return []
        return parser.zdjecia(tekst)

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        return mapper.na_aukcje(surowa, source_id, teraz)
