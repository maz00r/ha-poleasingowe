"""Repozytoria — jedyne miejsce z SQL-em (SPEC.md §6.2).

Zapytania jako stałe modułowe, **nigdy sklejane f-stringami**; parametry
wyłącznie przez placeholdery (SPEC.md §5).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.domain.entities import (
    Auction,
    PriceSnapshot,
    RunLog,
    Source,
    WatchlistEntry,
)
from app.domain.enums import (
    AuctionStatus,
    AuthState,
    Currency,
    FinalPriceState,
    PollTier,
)
from app.domain.value_objects import Mileage, Money, Vin

# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

_SOURCE_KOLUMNY = """
    id, key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures,
    overtime_window_seconds, overtime_extension_seconds, overtime_cap_seconds
"""

SQL_SOURCE_UPSERT = f"""
INSERT INTO app.source (
    key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures,
    overtime_window_seconds, overtime_extension_seconds, overtime_cap_seconds
) VALUES (%(key)s, %(name)s, %(enabled)s, %(sweep_interval_seconds)s,
          %(rate_limit_per_minute)s, %(floor_seconds)s, %(auth_state)s,
          %(consecutive_auth_failures)s, %(overtime_window_seconds)s,
          %(overtime_extension_seconds)s, %(overtime_cap_seconds)s)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    sweep_interval_seconds = EXCLUDED.sweep_interval_seconds,
    rate_limit_per_minute = EXCLUDED.rate_limit_per_minute,
    floor_seconds = EXCLUDED.floor_seconds,
    auth_state = EXCLUDED.auth_state,
    consecutive_auth_failures = EXCLUDED.consecutive_auth_failures,
    overtime_window_seconds = EXCLUDED.overtime_window_seconds,
    overtime_extension_seconds = EXCLUDED.overtime_extension_seconds,
    overtime_cap_seconds = EXCLUDED.overtime_cap_seconds
RETURNING {_SOURCE_KOLUMNY}
"""

SQL_SOURCE_PO_KLUCZU = f"SELECT {_SOURCE_KOLUMNY} FROM app.source WHERE key = %s"
SQL_SOURCE_WLACZONE = (
    f"SELECT {_SOURCE_KOLUMNY} FROM app.source WHERE enabled ORDER BY key"
)

_AUCTION_KOLUMNY = """
    id, source_id, external_id, url, make, model, variant, year, mileage_km,
    fuel, gearbox, engine_ccm, engine_hp, vin, body, color, location, seller,
    price_start, price_current, currency, bid_count, bid_increment_raw,
    ends_at, status, first_seen_at, last_seen_at, content_hash, raw_json,
    next_poll_at, poll_tier, consecutive_failures, final_price_state,
    last_price_lead_seconds, duplicate_of
"""

SQL_AUCTION_UPSERT = f"""
INSERT INTO app.auction (
    source_id, external_id, url, make, model, variant, year, mileage_km,
    fuel, gearbox, engine_ccm, engine_hp, vin, body, color, location, seller,
    price_start, price_current, currency, bid_count, bid_increment_raw,
    ends_at, status, first_seen_at, last_seen_at, content_hash, raw_json,
    next_poll_at, poll_tier, consecutive_failures, final_price_state,
    last_price_lead_seconds, duplicate_of
) VALUES (
    %(source_id)s, %(external_id)s, %(url)s, %(make)s, %(model)s, %(variant)s,
    %(year)s, %(mileage_km)s, %(fuel)s, %(gearbox)s, %(engine_ccm)s,
    %(engine_hp)s, %(vin)s, %(body)s, %(color)s, %(location)s, %(seller)s,
    %(price_start)s, %(price_current)s, %(currency)s, %(bid_count)s,
    %(bid_increment_raw)s, %(ends_at)s, %(status)s, %(first_seen_at)s,
    %(last_seen_at)s, %(content_hash)s, %(raw_json)s, %(next_poll_at)s,
    %(poll_tier)s, %(consecutive_failures)s, %(final_price_state)s,
    %(last_price_lead_seconds)s, %(duplicate_of)s
)
ON CONFLICT (source_id, external_id) DO UPDATE SET
    url = EXCLUDED.url,
    make = EXCLUDED.make, model = EXCLUDED.model, variant = EXCLUDED.variant,
    year = EXCLUDED.year, mileage_km = EXCLUDED.mileage_km,
    fuel = EXCLUDED.fuel, gearbox = EXCLUDED.gearbox,
    engine_ccm = EXCLUDED.engine_ccm, engine_hp = EXCLUDED.engine_hp,
    vin = EXCLUDED.vin, body = EXCLUDED.body, color = EXCLUDED.color,
    location = EXCLUDED.location, seller = EXCLUDED.seller,
    price_start = EXCLUDED.price_start, price_current = EXCLUDED.price_current,
    currency = EXCLUDED.currency, bid_count = EXCLUDED.bid_count,
    bid_increment_raw = EXCLUDED.bid_increment_raw,
    ends_at = EXCLUDED.ends_at, status = EXCLUDED.status,
    last_seen_at = EXCLUDED.last_seen_at,
    content_hash = EXCLUDED.content_hash, raw_json = EXCLUDED.raw_json,
    next_poll_at = EXCLUDED.next_poll_at, poll_tier = EXCLUDED.poll_tier,
    consecutive_failures = EXCLUDED.consecutive_failures,
    final_price_state = EXCLUDED.final_price_state,
    last_price_lead_seconds = EXCLUDED.last_price_lead_seconds,
    duplicate_of = EXCLUDED.duplicate_of
RETURNING {_AUCTION_KOLUMNY}
"""

SQL_AUCTION_PO_KLUCZU = (
    f"SELECT {_AUCTION_KOLUMNY} FROM app.auction "
    "WHERE source_id = %s AND external_id = %s"
)

# Zapytanie wykonywane najczesciej w calym systemie — musi trafiac w indeks
# czesciowy auction_next_poll_active_idx (SPEC.md §8.3).
SQL_AUCTION_DO_ODPYTU = f"""
SELECT {_AUCTION_KOLUMNY} FROM app.auction
WHERE status = 'ACTIVE' AND next_poll_at IS NOT NULL AND next_poll_at <= %s
ORDER BY ends_at NULLS LAST
LIMIT %s
"""

SQL_AUCTION_PO_VIN = (
    f"SELECT {_AUCTION_KOLUMNY} FROM app.auction WHERE vin = %s ORDER BY id"
)

SQL_AUCTION_ODNOTUJ_WIDZIANA = "UPDATE app.auction SET last_seen_at = %s WHERE id = %s"

_SNAPSHOT_KOLUMNY = "id, auction_id, ts, price, currency, bid_count, ends_at, bid_gap"

SQL_SNAPSHOT_OSTATNI = f"""
SELECT {_SNAPSHOT_KOLUMNY} FROM app.price_snapshot
WHERE auction_id = %s ORDER BY ts DESC, id DESC LIMIT 1
"""

SQL_SNAPSHOT_INSERT = f"""
INSERT INTO app.price_snapshot
    (auction_id, ts, price, currency, bid_count, ends_at, bid_gap)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING {_SNAPSHOT_KOLUMNY}
"""

SQL_SNAPSHOT_HISTORIA = f"""
SELECT {_SNAPSHOT_KOLUMNY} FROM app.price_snapshot
WHERE auction_id = %s ORDER BY ts, id
"""

SQL_WATCHLIST_DODAJ = """
INSERT INTO app.watchlist (auction_id, note, target_price, currency, added_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (auction_id) DO UPDATE SET
    note = EXCLUDED.note,
    target_price = EXCLUDED.target_price,
    currency = EXCLUDED.currency
RETURNING id, auction_id, note, target_price, currency, added_at
"""
SQL_WATCHLIST_USUN = "DELETE FROM app.watchlist WHERE auction_id = %s"
SQL_WATCHLIST_JEST = "SELECT 1 FROM app.watchlist WHERE auction_id = %s"

SQL_RUNLOG_START = """
INSERT INTO app.run_log (source_id, started_at)
VALUES (%s, %s)
RETURNING id, source_id, started_at, finished_at, new_count, changed_count,
          error_count, errors, rss_bytes, database_bytes, notes
"""
SQL_RUNLOG_KONIEC = """
UPDATE app.run_log SET
    finished_at = %s, new_count = %s, changed_count = %s, error_count = %s,
    errors = %s, rss_bytes = %s, database_bytes = %s, notes = %s
WHERE id = %s
RETURNING id, source_id, started_at, finished_at, new_count, changed_count,
          error_count, errors, rss_bytes, database_bytes, notes
"""


# --------------------------------------------------------------------------
# Mapowanie wiersz <-> encja
# --------------------------------------------------------------------------


def _money(kwota: Decimal | None, waluta: str) -> Money | None:
    return None if kwota is None else Money(kwota, Currency(waluta))


def _na_source(w: dict[str, Any]) -> Source:
    return Source(
        id=w["id"],
        key=w["key"],
        name=w["name"],
        enabled=w["enabled"],
        sweep_interval_seconds=w["sweep_interval_seconds"],
        rate_limit_per_minute=w["rate_limit_per_minute"],
        floor_seconds=w["floor_seconds"],
        auth_state=AuthState(w["auth_state"]),
        consecutive_auth_failures=w["consecutive_auth_failures"],
        overtime_window_seconds=w["overtime_window_seconds"],
        overtime_extension_seconds=w["overtime_extension_seconds"],
        overtime_cap_seconds=w["overtime_cap_seconds"],
    )


def _na_auction(w: dict[str, Any]) -> Auction:
    return Auction(
        id=w["id"],
        source_id=w["source_id"],
        external_id=w["external_id"],
        url=w["url"],
        make=w["make"],
        model=w["model"],
        variant=w["variant"],
        year=w["year"],
        mileage=None if w["mileage_km"] is None else Mileage(w["mileage_km"]),
        fuel=w["fuel"],
        gearbox=w["gearbox"],
        engine_ccm=w["engine_ccm"],
        engine_hp=w["engine_hp"],
        vin=None if w["vin"] is None else Vin(w["vin"]),
        body=w["body"],
        color=w["color"],
        location=w["location"],
        seller=w["seller"],
        price_start=_money(w["price_start"], w["currency"]),
        price_current=_money(w["price_current"], w["currency"]),
        bid_count=w["bid_count"],
        bid_increment_raw=w["bid_increment_raw"],
        ends_at=w["ends_at"],
        status=AuctionStatus(w["status"]),
        first_seen_at=w["first_seen_at"],
        last_seen_at=w["last_seen_at"],
        content_hash=w["content_hash"],
        raw_json=w["raw_json"],
        next_poll_at=w["next_poll_at"],
        poll_tier=PollTier(w["poll_tier"]),
        consecutive_failures=w["consecutive_failures"],
        final_price_state=FinalPriceState(w["final_price_state"]),
        last_price_lead_seconds=w["last_price_lead_seconds"],
        duplicate_of=w["duplicate_of"],
    )


def _na_snapshot(w: dict[str, Any]) -> PriceSnapshot:
    return PriceSnapshot(
        id=w["id"],
        auction_id=w["auction_id"],
        ts=w["ts"],
        price=Money(w["price"], Currency(w["currency"])),
        bid_count=w["bid_count"],
        ends_at=w["ends_at"],
        bid_gap=w["bid_gap"],
    )


# --------------------------------------------------------------------------
# Repozytoria
# --------------------------------------------------------------------------


class PgSourceRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz(self, source: Source) -> Source:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_SOURCE_UPSERT,
                {
                    "key": source.key,
                    "name": source.name,
                    "enabled": source.enabled,
                    "sweep_interval_seconds": source.sweep_interval_seconds,
                    "rate_limit_per_minute": source.rate_limit_per_minute,
                    "floor_seconds": source.floor_seconds,
                    "auth_state": source.auth_state.value,
                    "consecutive_auth_failures": source.consecutive_auth_failures,
                    "overtime_window_seconds": source.overtime_window_seconds,
                    "overtime_extension_seconds": source.overtime_extension_seconds,
                    "overtime_cap_seconds": source.overtime_cap_seconds,
                },
            )
            wiersz = await cur.fetchone()
        assert wiersz is not None
        return _na_source(wiersz)

    async def po_kluczu(self, key: str) -> Source | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SOURCE_PO_KLUCZU, (key,))
            wiersz = await cur.fetchone()
        return None if wiersz is None else _na_source(wiersz)

    async def wlaczone(self) -> Sequence[Source]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SOURCE_WLACZONE)
            return [_na_source(w) for w in await cur.fetchall()]


class PgAuctionRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz(self, auction: Auction) -> Auction:
        cena = auction.price_current or auction.price_start
        waluta = (cena.currency if cena else Currency.PLN).value
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_AUCTION_UPSERT,
                {
                    "source_id": auction.source_id,
                    "external_id": auction.external_id,
                    "url": auction.url,
                    "make": auction.make,
                    "model": auction.model,
                    "variant": auction.variant,
                    "year": auction.year,
                    "mileage_km": None
                    if auction.mileage is None
                    else auction.mileage.km,
                    "fuel": auction.fuel,
                    "gearbox": auction.gearbox,
                    "engine_ccm": auction.engine_ccm,
                    "engine_hp": auction.engine_hp,
                    "vin": None if auction.vin is None else auction.vin.value,
                    "body": auction.body,
                    "color": auction.color,
                    "location": auction.location,
                    "seller": auction.seller,
                    "price_start": (
                        None
                        if auction.price_start is None
                        else auction.price_start.amount
                    ),
                    "price_current": (
                        None
                        if auction.price_current is None
                        else auction.price_current.amount
                    ),
                    "currency": waluta,
                    "bid_count": auction.bid_count,
                    "bid_increment_raw": auction.bid_increment_raw,
                    "ends_at": auction.ends_at,
                    "status": auction.status.value,
                    "first_seen_at": auction.first_seen_at,
                    "last_seen_at": auction.last_seen_at,
                    "content_hash": auction.content_hash,
                    "raw_json": (
                        None
                        if auction.raw_json is None
                        else json.dumps(auction.raw_json)
                    ),
                    "next_poll_at": auction.next_poll_at,
                    "poll_tier": auction.poll_tier.value,
                    "consecutive_failures": auction.consecutive_failures,
                    "final_price_state": auction.final_price_state.value,
                    "last_price_lead_seconds": auction.last_price_lead_seconds,
                    "duplicate_of": auction.duplicate_of,
                },
            )
            wiersz = await cur.fetchone()
        assert wiersz is not None
        return _na_auction(wiersz)

    async def po_kluczu_naturalnym(
        self, source_id: int, external_id: str
    ) -> Auction | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_AUCTION_PO_KLUCZU, (source_id, external_id))
            wiersz = await cur.fetchone()
        return None if wiersz is None else _na_auction(wiersz)

    async def do_odpytu(self, teraz: dt.datetime, limit: int) -> Sequence[Auction]:
        """Aukcje z `next_poll_at <= teraz`, posortowane po najbliższym końcu.

        SPEC.md §11.1 — dispatcher bierze je w tej kolejności; §11.6 daje
        priorytet bliższemu `ends_at`.
        """
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_AUCTION_DO_ODPYTU, (teraz, limit))
            return [_na_auction(w) for w in await cur.fetchall()]

    async def po_vin(self, vin: Vin) -> Sequence[Auction]:
        """Podstawa deduplikacji między serwisami (SPEC.md §8.4)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_AUCTION_PO_VIN, (vin.value,))
            return [_na_auction(w) for w in await cur.fetchall()]

    async def odnotuj_widziana(self, auction_id: int, teraz: dt.datetime) -> None:
        """Odpyt bez zmiany aktualizuje tylko `last_seen_at` (SPEC.md §8.4)."""
        await self._conn.execute(SQL_AUCTION_ODNOTUJ_WIDZIANA, (teraz, auction_id))


class PgSnapshotRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz_jesli_zmienil_sie(
        self, snapshot: PriceSnapshot
    ) -> PriceSnapshot | None:
        """Zapisuje snapshot **wyłącznie** przy zmianie (SPEC.md §8.4).

        Bez tej reguły dogrywka generuje setki identycznych wierszy na aukcję
        i niepotrzebnie obciąża instancję dzieloną z TeslaMate.

        Przy okazji wylicza `bid_gap` (SPEC.md §11.8): przyrost `bid_count`
        ponad 1, liczony względem **poprzedniego snapshotu tej aukcji**.
        Pierwszy snapshot dostaje `None`, nie `0` — w chwili pierwszej
        obserwacji aukcja mogła już mieć oferty.

        Zwraca zapisany snapshot albo `None`, gdy nic się nie zmieniło.
        """
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SNAPSHOT_OSTATNI, (snapshot.auction_id,))
            wiersz = await cur.fetchone()

        poprzedni = None if wiersz is None else _na_snapshot(wiersz)

        if poprzedni is not None:
            bez_zmian = (
                poprzedni.price == snapshot.price
                and poprzedni.bid_count == snapshot.bid_count
                and poprzedni.ends_at == snapshot.ends_at
            )
            if bez_zmian:
                return None

        bid_gap: int | None = None
        if (
            poprzedni is not None
            and poprzedni.bid_count is not None
            and snapshot.bid_count is not None
        ):
            bid_gap = max(snapshot.bid_count - poprzedni.bid_count - 1, 0)

        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_SNAPSHOT_INSERT,
                (
                    snapshot.auction_id,
                    snapshot.ts,
                    snapshot.price.amount,
                    snapshot.price.currency.value,
                    snapshot.bid_count,
                    snapshot.ends_at,
                    bid_gap,
                ),
            )
            zapisany = await cur.fetchone()
        assert zapisany is not None
        return _na_snapshot(zapisany)

    async def historia(self, auction_id: int) -> Sequence[PriceSnapshot]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SNAPSHOT_HISTORIA, (auction_id,))
            return [_na_snapshot(w) for w in await cur.fetchall()]


class PgWatchlistRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def dodaj(self, wpis: WatchlistEntry) -> WatchlistEntry:
        cena = wpis.target_price
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_WATCHLIST_DODAJ,
                (
                    wpis.auction_id,
                    wpis.note,
                    None if cena is None else cena.amount,
                    (cena.currency if cena else Currency.PLN).value,
                    wpis.added_at,
                ),
            )
            w = await cur.fetchone()
        assert w is not None
        return WatchlistEntry(
            id=w["id"],
            auction_id=w["auction_id"],
            note=w["note"],
            target_price=_money(w["target_price"], w["currency"]),
            added_at=w["added_at"],
        )

    async def usun(self, auction_id: int) -> bool:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_WATCHLIST_USUN, (auction_id,))
            return cur.rowcount > 0

    async def obserwowana(self, auction_id: int) -> bool:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_WATCHLIST_JEST, (auction_id,))
            return await cur.fetchone() is not None


class PgRunLogRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    @staticmethod
    def _na_runlog(w: dict[str, Any]) -> RunLog:
        return RunLog(
            id=w["id"],
            source_id=w["source_id"],
            started_at=w["started_at"],
            finished_at=w["finished_at"],
            new_count=w["new_count"],
            changed_count=w["changed_count"],
            error_count=w["error_count"],
            errors=list(w["errors"]),
            rss_bytes=w["rss_bytes"],
            database_bytes=w["database_bytes"],
            notes=w["notes"],
        )

    async def rozpocznij(self, wpis: RunLog) -> RunLog:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_RUNLOG_START, (wpis.source_id, wpis.started_at))
            w = await cur.fetchone()
        assert w is not None
        return self._na_runlog(w)

    async def zakoncz(self, wpis: RunLog) -> RunLog:
        if wpis.id is None:
            raise ValueError("zakoncz() wymaga wpisu z id — najpierw rozpocznij()")
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_RUNLOG_KONIEC,
                (
                    wpis.finished_at,
                    wpis.new_count,
                    wpis.changed_count,
                    wpis.error_count,
                    json.dumps(wpis.errors),
                    wpis.rss_bytes,
                    wpis.database_bytes,
                    wpis.notes,
                    wpis.id,
                ),
            )
            w = await cur.fetchone()
        assert w is not None
        return self._na_runlog(w)


class PgUnitOfWork:
    """Jedna transakcja na use case (SPEC.md §6.2).

    Commit na wyjściu, rollback na wyjątku — obsługiwane przez
    `conn.transaction()`.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self._transakcja: Any = None
        self.source = PgSourceRepository(conn)
        self.auction = PgAuctionRepository(conn)
        self.snapshot = PgSnapshotRepository(conn)
        self.watchlist = PgWatchlistRepository(conn)
        self.run_log = PgRunLogRepository(conn)

    async def __aenter__(self) -> PgUnitOfWork:
        self._transakcja = self._conn.transaction()
        await self._transakcja.__aenter__()
        return self

    async def __aexit__(self, *wyjatek: Any) -> None:
        assert self._transakcja is not None
        await self._transakcja.__aexit__(*wyjatek)
        self._transakcja = None
