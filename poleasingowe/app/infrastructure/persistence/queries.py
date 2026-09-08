"""Strona odczytu dla interfejsu (SPEC.md §12).

Zapytania listy są **składane**, bo filtrów jest kilkanaście i każda ich
kombinacja jest dozwolona. Składamy je przez `psycopg.sql`, nigdy przez
f-stringi: nazwy kolumn pochodzą ze słownika w tym pliku, a wszystkie
wartości idą osobno jako parametry.

Paginacja jest **keyset, nie `OFFSET`** (§12). `OFFSET 5000` każe bazie
przeczytać i wyrzucić pięć tysięcy wierszy przy każdym przewinięciu, a przy
danych, które w międzyczasie się zmieniły, gubi albo powtarza pozycje.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Final

from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

from app.application.read_models import (
    Kryteria,
    Kursor,
    PozycjaListy,
    Sortowanie,
    StanZrodla,
    Strona,
    Szczegoly,
)
from app.domain.enums import AuctionStatus, Currency, FinalPriceState, PollTier
from app.domain.value_objects import Money


class _Klucz:
    """Opis kolumny sortowania: wyrażenie, kierunek i typ w kursorze."""

    __slots__ = ("kolumna", "malejaco", "rzutowanie")

    def __init__(self, kolumna: str, *, malejaco: bool, rzutowanie: str) -> None:
        self.kolumna = kolumna
        self.malejaco = malejaco
        self.rzutowanie = rzutowanie


# Jedyne dozwolone klucze sortowania. Nazwa kolumny nigdy nie pochodzi
# z zapytania HTTP — przychodzi stąd, po dopasowaniu do wartości `Sortowanie`.
KLUCZE: Final[dict[Sortowanie, _Klucz]] = {
    Sortowanie.KONIEC_ROSNACO: _Klucz(
        "ends_at", malejaco=False, rzutowanie="timestamptz"
    ),
    Sortowanie.KONIEC_MALEJACO: _Klucz(
        "ends_at", malejaco=True, rzutowanie="timestamptz"
    ),
    Sortowanie.CENA_ROSNACO: _Klucz(
        "price_current", malejaco=False, rzutowanie="numeric"
    ),
    Sortowanie.CENA_MALEJACO: _Klucz(
        "price_current", malejaco=True, rzutowanie="numeric"
    ),
    Sortowanie.ROCZNIK_MALEJACO: _Klucz("year", malejaco=True, rzutowanie="integer"),
    Sortowanie.PRZEBIEG_ROSNACO: _Klucz(
        "mileage_km", malejaco=False, rzutowanie="integer"
    ),
    Sortowanie.NAJNOWSZE: _Klucz(
        "first_seen_at", malejaco=True, rzutowanie="timestamptz"
    ),
}

KOLUMNY_LISTY = sql.SQL("""
    a.id, s.key AS source_key, a.external_id, a.url, a.status,
    a.make, a.model, a.variant, a.year, a.mileage_km, a.fuel, a.gearbox,
    a.location, a.price_start, a.price_current, a.currency, a.bid_count,
    a.ends_at, a.first_seen_at, a.final_price_state,
    (w.auction_id IS NOT NULL) AS obserwowana,
    w.target_price, w.currency AS target_currency, w.note
""")

ZRODLO_LISTY = sql.SQL("""
FROM app.auction AS a
JOIN app.source AS s ON s.id = a.source_id
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id
""")

SQL_SZCZEGOLY = sql.SQL("""
SELECT
    a.id, s.key AS source_key, a.external_id, a.url, a.status,
    a.make, a.model, a.variant, a.year, a.mileage_km, a.fuel, a.gearbox,
    a.location, a.price_start, a.price_current, a.currency, a.bid_count,
    a.ends_at, a.first_seen_at, a.final_price_state,
    (w.auction_id IS NOT NULL) AS obserwowana,
    w.target_price, w.currency AS target_currency, w.note,
    a.vin, a.body, a.color, a.engine_ccm, a.engine_hp, a.seller,
    a.bid_increment_raw, a.last_seen_at, a.next_poll_at, a.poll_tier,
    a.last_price_lead_seconds, a.duplicate_of
FROM app.auction AS a
JOIN app.source AS s ON s.id = a.source_id
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id
WHERE a.id = %s
""")

# COLLATE "pl-PL-x-icu" — bez tego "Ż" ląduje za "Z" wg bajtów, a nie wg
# polskiej kolejności (SPEC.md §8.3).
# UNION w podzapytaniu, bo `ORDER BY` nad samym UNION-em przyjmuje wylacznie
# nazwy kolumn wynikowych — `COLLATE` jest wyrazeniem i Postgres go tam
# odrzuca ("Only result column names can be used").
SQL_WARTOSCI_FILTROW = sql.SQL("""
SELECT pole, wartosc FROM (
    SELECT 'marka' AS pole, a.make AS wartosc FROM app.auction AS a
        WHERE a.make IS NOT NULL
    UNION
    SELECT 'paliwo', a.fuel FROM app.auction AS a WHERE a.fuel IS NOT NULL
    UNION
    SELECT 'skrzynia', a.gearbox FROM app.auction AS a
        WHERE a.gearbox IS NOT NULL
    UNION
    SELECT 'lokalizacja', a.location FROM app.auction AS a
        WHERE a.location IS NOT NULL
    UNION
    SELECT 'zrodlo', s.key FROM app.source AS s
) AS wartosci
ORDER BY pole, wartosc COLLATE "pl-PL-x-icu"
""")

SQL_DIAGNOSTYKA = sql.SQL("""
SELECT source_key, source_name, enabled, auth_state,
       consecutive_auth_failures, rate_limit_per_minute, floor_seconds,
       aktywne_aukcje, closing_ladder_seconds, bid_count_semantics,
       last_run_started_at, last_run_finished_at, last_run_new,
       last_run_changed, last_run_errors, last_run_rss_bytes,
       last_run_database_bytes
FROM reporting.v_source_health
ORDER BY source_key
""")

SQL_ROZMIAR_BAZY = sql.SQL("SELECT pg_database_size(current_database())")
# `EXISTS` zatrzymuje się na pierwszym wierszu — `COUNT(*)` przeczytałby
# całą tabelę tylko po to, żeby odpowiedzieć „tak".
SQL_SA_AUKCJE = sql.SQL("SELECT EXISTS (SELECT 1 FROM app.auction)")
SQL_CZAS_SERWERA = sql.SQL("SELECT now()")


def _money(kwota: Decimal | None, waluta: str | None) -> Money | None:
    if kwota is None or waluta is None:
        return None
    return Money(kwota, Currency(waluta))


def _na_pozycje(w: dict[str, Any]) -> PozycjaListy:
    return PozycjaListy(
        id=w["id"],
        source_key=w["source_key"],
        external_id=w["external_id"],
        url=w["url"],
        status=AuctionStatus(w["status"]),
        make=w["make"],
        model=w["model"],
        variant=w["variant"],
        year=w["year"],
        mileage_km=w["mileage_km"],
        fuel=w["fuel"],
        gearbox=w["gearbox"],
        location=w["location"],
        price_start=_money(w["price_start"], w["currency"]),
        price_current=_money(w["price_current"], w["currency"]),
        bid_count=w["bid_count"],
        ends_at=w["ends_at"],
        first_seen_at=w["first_seen_at"],
        final_price_state=FinalPriceState(w["final_price_state"]),
        obserwowana=w["obserwowana"],
        cena_docelowa=_money(w["target_price"], w["target_currency"]),
        notatka=w["note"],
    )


def _wartosc_kursora(wiersz: dict[str, Any], klucz: _Klucz) -> str | None:
    """Wartość klucza sortowania ostatniego wiersza, w postaci tekstowej.

    Tekst, nie typ natywny, bo kursor jedzie przez adres URL. Rzutowanie
    z powrotem robi Postgres — stąd `rzutowanie` w opisie klucza.
    """
    wartosc = wiersz[klucz.kolumna]
    if wartosc is None:
        return None
    if isinstance(wartosc, dt.datetime):
        return wartosc.isoformat()
    return str(wartosc)


def _warunki(kryteria: Kryteria) -> tuple[list[sql.Composable], dict[str, Any]]:
    """Filtry z §12 jako lista warunków `AND` plus parametry."""
    warunki: list[sql.Composable] = [
        # Duplikaty po VIN (§8.4) nie mają prawa zaśmiecać listy. Na karcie
        # szczegółów zostają widoczne przez `duplicate_of`.
        sql.SQL("a.duplicate_of IS NULL")
    ]
    parametry: dict[str, Any] = {}

    if kryteria.status is not None:
        warunki.append(sql.SQL("a.status = %(status)s"))
        parametry["status"] = kryteria.status.value
    if kryteria.szukaj:
        warunki.append(
            sql.SQL(
                "(a.make ILIKE %(szukaj)s OR a.model ILIKE %(szukaj)s"
                " OR a.variant ILIKE %(szukaj)s OR a.external_id ILIKE %(szukaj)s"
                " OR a.vin ILIKE %(szukaj)s)"
            )
        )
        parametry["szukaj"] = f"%{kryteria.szukaj}%"
    if kryteria.marka:
        warunki.append(sql.SQL("a.make = %(marka)s"))
        parametry["marka"] = kryteria.marka
    if kryteria.model:
        warunki.append(sql.SQL("a.model = %(model)s"))
        parametry["model"] = kryteria.model
    if kryteria.zrodlo:
        warunki.append(sql.SQL("s.key = %(zrodlo)s"))
        parametry["zrodlo"] = kryteria.zrodlo
    if kryteria.paliwo:
        warunki.append(sql.SQL("a.fuel = %(paliwo)s"))
        parametry["paliwo"] = kryteria.paliwo
    if kryteria.skrzynia:
        warunki.append(sql.SQL("a.gearbox = %(skrzynia)s"))
        parametry["skrzynia"] = kryteria.skrzynia
    if kryteria.lokalizacja:
        warunki.append(sql.SQL("a.location = %(lokalizacja)s"))
        parametry["lokalizacja"] = kryteria.lokalizacja
    if kryteria.cena_od is not None:
        warunki.append(sql.SQL("a.price_current >= %(cena_od)s"))
        parametry["cena_od"] = kryteria.cena_od
    if kryteria.cena_do is not None:
        warunki.append(sql.SQL("a.price_current <= %(cena_do)s"))
        parametry["cena_do"] = kryteria.cena_do
    if kryteria.rocznik_od is not None:
        warunki.append(sql.SQL("a.year >= %(rocznik_od)s"))
        parametry["rocznik_od"] = kryteria.rocznik_od
    if kryteria.rocznik_do is not None:
        warunki.append(sql.SQL("a.year <= %(rocznik_do)s"))
        parametry["rocznik_do"] = kryteria.rocznik_do
    if kryteria.przebieg_do is not None:
        warunki.append(sql.SQL("a.mileage_km <= %(przebieg_do)s"))
        parametry["przebieg_do"] = kryteria.przebieg_do
    if kryteria.konczy_sie_w_h is not None:
        # Widok „kończą się w 24 h" (§12). Dolna granica to `now()`, żeby
        # aukcje po terminie nie wpadały tu razem z nadchodzącymi.
        warunki.append(
            sql.SQL(
                "a.ends_at >= now() AND a.ends_at <="
                " now() + make_interval(hours => %(w_godzinach)s)"
            )
        )
        parametry["w_godzinach"] = kryteria.konczy_sie_w_h
    if kryteria.tylko_obserwowane:
        warunki.append(sql.SQL("w.auction_id IS NOT NULL"))
    if kryteria.nowe_od is not None:
        warunki.append(sql.SQL("a.first_seen_at > %(nowe_od)s"))
        parametry["nowe_od"] = kryteria.nowe_od
    return warunki, parametry


def _warunek_kursora(
    klucz: _Klucz, kursor: Kursor, parametry: dict[str, Any]
) -> sql.Composable:
    """Predykat keyset dla `ORDER BY <klucz> [DESC] NULLS LAST, id ASC`.

    Trzy przypadki, nie jeden. Wiersze z pustym kluczem idą na koniec, więc
    kiedy kursor stoi już w tym ogonie, jedynym kryterium jest `id`. Gdy
    stoi przed nim, trzeba przepuścić zarówno dalsze wartości, jak i całą
    resztę z `NULL`-em — inaczej ostatnia strona po prostu znika.
    """
    parametry["kursor_id"] = kursor.id
    kolumna = sql.SQL("a.{}").format(sql.Identifier(klucz.kolumna))

    if kursor.wartosc is None:
        return sql.SQL("({kol} IS NULL AND a.id > %(kursor_id)s)").format(kol=kolumna)

    parametry["kursor_wartosc"] = kursor.wartosc
    wartosc = sql.SQL("%(kursor_wartosc)s::{}").format(sql.SQL(klucz.rzutowanie))
    porownanie = sql.SQL("<") if klucz.malejaco else sql.SQL(">")
    return sql.SQL(
        "({kol} {op} {war} OR {kol} IS NULL"
        " OR ({kol} = {war} AND a.id > %(kursor_id)s))"
    ).format(kol=kolumna, op=porownanie, war=wartosc)


def _sortowanie(klucz: _Klucz) -> sql.Composable:
    kierunek = sql.SQL("DESC") if klucz.malejaco else sql.SQL("ASC")
    return sql.SQL("ORDER BY a.{kol} {kier} NULLS LAST, a.id ASC").format(
        kol=sql.Identifier(klucz.kolumna), kier=kierunek
    )


class PgZapytania:
    """Implementacja portu `Zapytania` na psycopg 3."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def lista(
        self, kryteria: Kryteria, kursor: Kursor | None, limit: int
    ) -> Strona:
        klucz = KLUCZE[kryteria.sortowanie]
        warunki, parametry = _warunki(kryteria)
        if kursor is not None:
            warunki.append(_warunek_kursora(klucz, kursor, parametry))

        # Pobieramy jeden wiersz PONAD limit. To tańsze niż COUNT(*) po tych
        # samych filtrach, a odpowiada na jedyne pytanie, które lista zadaje:
        # czy jest jeszcze co pokazać.
        parametry["limit"] = limit + 1
        zapytanie = sql.SQL(" ").join(
            [
                sql.SQL("SELECT"),
                KOLUMNY_LISTY,
                ZRODLO_LISTY,
                sql.SQL("WHERE"),
                sql.SQL(" AND ").join(warunki),
                _sortowanie(klucz),
                sql.SQL("LIMIT %(limit)s"),
            ]
        )
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(zapytanie, parametry)
            wiersze = await cur.fetchall()

        ma_wiecej = len(wiersze) > limit
        widoczne = wiersze[:limit]
        nastepny = (
            Kursor(_wartosc_kursora(widoczne[-1], klucz), widoczne[-1]["id"]).zakoduj()
            if ma_wiecej and widoczne
            else None
        )
        return Strona(
            pozycje=tuple(_na_pozycje(w) for w in widoczne), kursor_dalej=nastepny
        )

    async def szczegoly(self, auction_id: int) -> Szczegoly | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SZCZEGOLY, (auction_id,))
            wiersz = await cur.fetchone()
        if wiersz is None:
            return None
        return Szczegoly(
            pozycja=_na_pozycje(wiersz),
            vin=wiersz["vin"],
            body=wiersz["body"],
            color=wiersz["color"],
            engine_ccm=wiersz["engine_ccm"],
            engine_hp=wiersz["engine_hp"],
            seller=wiersz["seller"],
            bid_increment_raw=wiersz["bid_increment_raw"],
            last_seen_at=wiersz["last_seen_at"],
            next_poll_at=wiersz["next_poll_at"],
            poll_tier=PollTier(wiersz["poll_tier"]),
            last_price_lead_seconds=wiersz["last_price_lead_seconds"],
            duplicate_of=wiersz["duplicate_of"],
        )

    async def wartosci_filtrow(self) -> dict[str, tuple[str, ...]]:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_WARTOSCI_FILTROW)
            wiersze = await cur.fetchall()
        wynik: dict[str, list[str]] = {
            "marka": [],
            "paliwo": [],
            "skrzynia": [],
            "lokalizacja": [],
            "zrodlo": [],
        }
        for pole, wartosc in wiersze:
            wynik.setdefault(pole, []).append(wartosc)
        return {k: tuple(v) for k, v in wynik.items()}

    async def diagnostyka(self) -> tuple[StanZrodla, ...]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_DIAGNOSTYKA)
            wiersze = await cur.fetchall()
        return tuple(
            StanZrodla(
                source_key=w["source_key"],
                source_name=w["source_name"],
                enabled=w["enabled"],
                auth_state=w["auth_state"],
                consecutive_auth_failures=w["consecutive_auth_failures"],
                rate_limit_per_minute=w["rate_limit_per_minute"],
                floor_seconds=w["floor_seconds"],
                aktywne_aukcje=w["aktywne_aukcje"] or 0,
                closing_ladder_seconds=tuple(w["closing_ladder_seconds"] or ()),
                bid_count_semantics=w["bid_count_semantics"],
                last_run_started_at=w["last_run_started_at"],
                last_run_finished_at=w["last_run_finished_at"],
                last_run_new=w["last_run_new"],
                last_run_changed=w["last_run_changed"],
                last_run_errors=w["last_run_errors"],
                last_run_rss_bytes=w["last_run_rss_bytes"],
                last_run_database_bytes=w["last_run_database_bytes"],
            )
            for w in wiersze
        )

    async def rozmiar_bazy(self) -> int | None:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_ROZMIAR_BAZY)
            wiersz = await cur.fetchone()
        if wiersz is None:
            return None
        rozmiar: int = wiersz[0]
        return rozmiar

    async def sa_jakiekolwiek_aukcje(self) -> bool:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_SA_AUKCJE)
            wiersz = await cur.fetchone()
        return bool(wiersz and wiersz[0])

    async def czas_serwera(self) -> dt.datetime:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_CZAS_SERWERA)
            wiersz = await cur.fetchone()
        assert wiersz is not None
        czas: dt.datetime = wiersz[0]
        return czas
