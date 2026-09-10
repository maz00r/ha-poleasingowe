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

from app.application.ports import (
    AuctionRepository,
    OfertaRepository,
    RunLogRepository,
    SavedFilterRepository,
    SnapshotRepository,
    SourceRepository,
    WatchlistRepository,
    WycenaRepository,
)
from app.domain.entities import (
    Auction,
    OfertaUczestnika,
    PriceSnapshot,
    RunLog,
    SavedFilter,
    Source,
    WatchlistEntry,
    WycenaAukcji,
)
from app.domain.enums import (
    AuctionStatus,
    AuthState,
    BidCountSemantics,
    Currency,
    FinalPriceState,
    PollTier,
    RodzajPojazdu,
    SweepStatus,
)
from app.domain.value_objects import Mileage, Money, Vin

# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

SQL_SOURCE_UPSERT = """
INSERT INTO app.source (
    key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures,
    overtime_window_seconds, overtime_extension_seconds, overtime_cap_seconds,
    closing_ladder_seconds, bid_history_ttl_seconds, bid_count_semantics,
    last_sweep_at, last_sweep_attempt_at, last_sweep_status
) VALUES (%(key)s, %(name)s, %(enabled)s, %(sweep_interval_seconds)s,
          %(rate_limit_per_minute)s, %(floor_seconds)s, %(auth_state)s,
          %(consecutive_auth_failures)s, %(overtime_window_seconds)s,
          %(overtime_extension_seconds)s, %(overtime_cap_seconds)s,
          %(closing_ladder_seconds)s, %(bid_history_ttl_seconds)s,
          %(bid_count_semantics)s, %(last_sweep_at)s, %(last_sweep_attempt_at)s,
          %(last_sweep_status)s)
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
    overtime_cap_seconds = EXCLUDED.overtime_cap_seconds,
    closing_ladder_seconds = EXCLUDED.closing_ladder_seconds,
    bid_history_ttl_seconds = EXCLUDED.bid_history_ttl_seconds,
    bid_count_semantics = EXCLUDED.bid_count_semantics,
    last_sweep_at = COALESCE(EXCLUDED.last_sweep_at, app.source.last_sweep_at),
    last_sweep_attempt_at = COALESCE(
        EXCLUDED.last_sweep_attempt_at, app.source.last_sweep_attempt_at
    ),
    last_sweep_status = CASE
        WHEN EXCLUDED.last_sweep_attempt_at IS NULL THEN app.source.last_sweep_status
        ELSE EXCLUDED.last_sweep_status
    END
RETURNING id, key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures,
    overtime_window_seconds, overtime_extension_seconds, overtime_cap_seconds,
    closing_ladder_seconds, bid_history_ttl_seconds, bid_count_semantics,
    last_sweep_at, last_sweep_attempt_at, last_sweep_status
"""

SQL_SOURCE_PO_KLUCZU = """
SELECT
    id, key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures, overtime_window_seconds,
    overtime_extension_seconds, overtime_cap_seconds, closing_ladder_seconds,
    bid_history_ttl_seconds, bid_count_semantics, last_sweep_at,
    last_sweep_attempt_at, last_sweep_status
FROM app.source WHERE key = %s
"""
SQL_SOURCE_WLACZONE = """
SELECT
    id, key, name, enabled, sweep_interval_seconds, rate_limit_per_minute,
    floor_seconds, auth_state, consecutive_auth_failures, overtime_window_seconds,
    overtime_extension_seconds, overtime_cap_seconds, closing_ladder_seconds,
    bid_history_ttl_seconds, bid_count_semantics, last_sweep_at,
    last_sweep_attempt_at, last_sweep_status
FROM app.source WHERE enabled ORDER BY key
"""

SQL_AUCTION_UPSERT = """
INSERT INTO app.auction (
    source_id, external_id, url, make, model, variant, year, mileage_km,
    fuel, gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color,
    location, seller,
    price_start, price_current, currency, bid_count, bid_increment_raw,
    ends_at, status, first_seen_at, last_seen_at, content_hash, raw_json,
    next_poll_at, poll_tier, consecutive_failures, final_price_state,
    last_price_lead_seconds, duplicate_of
) VALUES (
    %(source_id)s, %(external_id)s, %(url)s, %(make)s, %(model)s, %(variant)s,
    %(year)s, %(mileage_km)s, %(fuel)s, %(gearbox)s, %(engine_ccm)s,
    %(engine_hp)s, %(vin)s, %(body)s, %(vehicle_kind)s, %(color)s, %(location)s,
    %(seller)s,
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
    vin = EXCLUDED.vin, body = EXCLUDED.body,
    vehicle_kind = EXCLUDED.vehicle_kind, color = EXCLUDED.color,
    -- Brak lokalizacji w szczegółach nie może kasować wartości znalezionej
    -- wcześniej na liście. Pełna, nowa wartość nadal poprawia starą.
    location = COALESCE(EXCLUDED.location, app.auction.location),
    seller = EXCLUDED.seller,
    -- Ceny wywolawczej RAZ POZNANEJ nie tracimy. Wnioskujemy ja z
    -- `bid_count = 0` (§8.2), a to znika w chwili pierwszej oferty:
    -- nadpisanie NULL-em kasowaloby jedyna liczbe, ktorej juz nie da sie
    -- odzyskac — serwisy nie podaja ceny wywolawczej wprost.
    price_start = COALESCE(EXCLUDED.price_start, app.auction.price_start),
    price_current = EXCLUDED.price_current,
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
RETURNING id, source_id, external_id, url, make, model, variant, year, mileage_km,
    fuel, gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color,
    location, seller,
    price_start, price_current, currency, bid_count, bid_increment_raw,
    ends_at, status, first_seen_at, last_seen_at, content_hash, raw_json,
    next_poll_at, poll_tier, consecutive_failures, final_price_state,
    last_price_lead_seconds, duplicate_of
"""

# Zapis z przemiatu listy (SPEC.md §11.2, §8.4). RÓŻNI SIĘ od upsertu po
# odpycie szczegółów, i to jest cała jego racja bytu: **lista wie mniej**.
#
# poleasingowe.pl nie podaje na liście godziny zakończenia (RECON.md §4.2),
# więc gdyby przemiat nadpisywał `ends_at`, kasowałby dokładny termin
# odczytany wcześniej ze strony szczegółów — i harmonogram z §11.2 straciłby
# to, na czym stoi. Stąd `COALESCE(EXCLUDED.x, app.auction.x)`: przemiat
# **uzupełnia** brakujące pola, nigdy nie zastępuje wypełnionych pustką.
#
# `status`, `next_poll_at`, `poll_tier`, `content_hash` i `raw_json` nie są
# aktualizowane w ogóle — przemiat nie ma o nich wiedzy. W szczególności
# aukcja oznaczona jako ENDED po odpycie szczegółów nie ma prawa wrócić do
# ACTIVE tylko dlatego, że serwis nadal pokazuje ją na liście.
# Przemiat listy zapisuje TAKZE snapshot ceny (SPEC.md §8.4).
#
# Bez tego licznik ofert na karcie aukcji rozjezdzal sie z ostatnim wierszem
# historii: przemiat aktualizowal `auction.bid_count` w miejscu i nie zostawial
# po tej zmianie zadnego sladu. Karta pokazywala wtedy "3 oferty" u gory
# i "2" w historii — obie liczby prawdziwe, tylko z roznych chwil.
#
# To jest tez JEDYNE zrodlo historii ceny dla aukcji NIEOBSERWOWANEJ: takiej
# nie odpytujemy pojedynczo po raz drugi (§11.2), wiec bez tego jej przebieg
# licytacji konczyl sie na pierwszym odczycie.
#
# Regula zapisu jest ta sama co przy odpycie szczegolow — wylacznie przy
# zmianie ceny, liczby ofert albo terminu — wiec przemiat bez zmian nie
# tworzy ani jednego wiersza i wolumen zapisow pozostaje znikomy (§2).
SQL_AUCTION_Z_PRZEMIATU = """
WITH zapis AS (
INSERT INTO app.auction (
    source_id, external_id, url, make, model, variant, year, mileage_km,
    fuel, gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color,
    location, seller,
    price_start, price_current, currency, bid_count, bid_increment_raw,
    ends_at, status, first_seen_at, last_seen_at, next_poll_at, poll_tier
) VALUES (
    %(source_id)s, %(external_id)s, %(url)s, %(make)s, %(model)s, %(variant)s,
    %(year)s, %(mileage_km)s, %(fuel)s, %(gearbox)s, %(engine_ccm)s,
    %(engine_hp)s, %(vin)s, %(body)s, %(vehicle_kind)s, %(color)s, %(location)s,
    %(seller)s,
    %(price_start)s, %(price_current)s, %(currency)s, %(bid_count)s,
    %(bid_increment_raw)s, %(ends_at)s, %(status)s, %(first_seen_at)s,
    %(last_seen_at)s, %(next_poll_at)s, %(poll_tier)s
)
ON CONFLICT (source_id, external_id) DO UPDATE SET
    url = EXCLUDED.url,
    make = COALESCE(EXCLUDED.make, app.auction.make),
    model = COALESCE(EXCLUDED.model, app.auction.model),
    variant = COALESCE(EXCLUDED.variant, app.auction.variant),
    year = COALESCE(EXCLUDED.year, app.auction.year),
    mileage_km = COALESCE(EXCLUDED.mileage_km, app.auction.mileage_km),
    fuel = COALESCE(EXCLUDED.fuel, app.auction.fuel),
    gearbox = COALESCE(EXCLUDED.gearbox, app.auction.gearbox),
    engine_ccm = COALESCE(EXCLUDED.engine_ccm, app.auction.engine_ccm),
    engine_hp = COALESCE(EXCLUDED.engine_hp, app.auction.engine_hp),
    vin = COALESCE(EXCLUDED.vin, app.auction.vin),
    body = COALESCE(EXCLUDED.body, app.auction.body),
    -- `NIEZNANY` z przemiatu nie kasuje rodzaju rozpoznanego wczesniej ze
    -- strony szczegolow. COALESCE tu nie wystarcza: kolumna jest NOT NULL,
    -- wiec „nie wiem" przychodzi jako wartosc, a nie jako NULL.
    vehicle_kind = CASE
        WHEN EXCLUDED.vehicle_kind = 'NIEZNANY' THEN app.auction.vehicle_kind
        ELSE EXCLUDED.vehicle_kind
    END,
    color = COALESCE(EXCLUDED.color, app.auction.color),
    location = COALESCE(EXCLUDED.location, app.auction.location),
    seller = COALESCE(EXCLUDED.seller, app.auction.seller),
    price_start = COALESCE(EXCLUDED.price_start, app.auction.price_start),
    price_current = COALESCE(EXCLUDED.price_current, app.auction.price_current),
    bid_count = COALESCE(EXCLUDED.bid_count, app.auction.bid_count),
    bid_increment_raw = COALESCE(
        EXCLUDED.bid_increment_raw, app.auction.bid_increment_raw
    ),
    ends_at = COALESCE(EXCLUDED.ends_at, app.auction.ends_at),
    last_seen_at = EXCLUDED.last_seen_at,
    -- POWROT Z `DISAPPEARED`. Aukcje oznaczamy jako znikniete po dwoch
    -- przemiatach bez niej, ale przemiat potrafi urwac sie w polowie
    -- (paginacja, timeout, WAF) DWA RAZY POD RZAD. Bez tej linii aukcja,
    -- ktora znowu stoi na liscie, zostawala w archiwum na zawsze — bo nic
    -- w calym systemie nie cofalo tego statusu.
    --
    -- Wracamy WYLACZNIE z `DISAPPEARED` i wylacznie przed terminem.
    -- `ENDED` zostaje `ENDED`: poleasingowe trzyma zakonczone aukcje na
    -- liscie jeszcze dlugo po koncu i wskrzeszanie ich byloby gorszym
    -- bledem niz ten, ktory naprawiamy.
    status = CASE
        WHEN app.auction.status = 'DISAPPEARED'
             AND (EXCLUDED.ends_at IS NULL OR EXCLUDED.ends_at > now())
        THEN 'ACTIVE'
        ELSE app.auction.status
    END
RETURNING id, source_id, (xmax = 0) AS nowa,
          price_current, currency, bid_count, ends_at
),
poprzedni AS (
    SELECT DISTINCT ON (auction_id) auction_id, price, bid_count, ends_at
    FROM app.price_snapshot
    WHERE auction_id IN (SELECT id FROM zapis)
    ORDER BY auction_id, ts DESC, id DESC
),
snapshot AS (
    INSERT INTO app.price_snapshot
        (auction_id, ts, price, currency, bid_count, ends_at, bid_gap)
    SELECT
        z.id, %(last_seen_at)s, z.price_current, z.currency, z.bid_count,
        z.ends_at,
        -- `bid_gap` liczymy wylacznie tam, gdzie licznik faktycznie zlicza
        -- oferty (§11.8). Dla PARTICIPANTS i UNKNOWN zostaje NULL.
        CASE
            WHEN s.bid_count_semantics = 'OFFERS'
                 AND p.bid_count IS NOT NULL
                 AND z.bid_count IS NOT NULL
            THEN GREATEST(z.bid_count - p.bid_count - 1, 0)
        END
    FROM zapis AS z
    JOIN app.source AS s ON s.id = z.source_id
    LEFT JOIN poprzedni AS p ON p.auction_id = z.id
    WHERE z.price_current IS NOT NULL
      AND (
          p.auction_id IS NULL
          OR p.price IS DISTINCT FROM z.price_current
          OR p.bid_count IS DISTINCT FROM z.bid_count
          OR p.ends_at IS DISTINCT FROM z.ends_at
      )
)
SELECT id, nowa FROM zapis
"""

# Aukcja, ktora przestala pojawiac sie na liscie zrodla (SPEC.md §11.2).
#
# autoprzetarg.pl kasuje strone aukcji 10-15 s po terminie (RECON.md §3.4),
# ale zniknac moze tez oferta wycofana przez sprzedajacego — i wtedy nikt
# nam tego nie powie. Bez tego zapytania taka aukcja zostawala `ACTIVE`
# w nieskonczonosc.
#
# WARUNEK JEST OSTROZNY CELOWO: wymagamy, zeby aukcja nie pojawila sie
# w DWOCH kolejnych przemiatach (`last_seen_at < poprzedni przemiat`).
# Jeden przemiat potrafi urwac sie w polowie — paginacja, timeout, WAF —
# a wtedy "brak na liscie" znaczylby tylko "nie doszlismy do tej strony".
# Falszywe DISAPPEARED kasuje aukcje z widoku aktywnych, wiec wolimy sie
# spoznic o jeden cykl niz skasowac cos, co trwa.
#
# Aukcji PO TERMINIE nie ruszamy — nia zajmuje sie faza domkniecia z §11.5,
# ktora ma szanse zlapac cene koncowa. Nadpisanie jej statusem DISAPPEARED
# zabraloby te szanse.
SQL_AUCTION_OZNACZ_ZNIKNIETE = """
UPDATE app.auction
SET status = 'DISAPPEARED',
    next_poll_at = NULL,
    poll_tier = 'IDLE',
    final_price_state = CASE
        WHEN final_price_state = 'UNKNOWN' THEN 'LAST_SEEN'
        ELSE final_price_state
    END
WHERE source_id = %(source_id)s
  AND status = 'ACTIVE'
  AND last_seen_at < %(poprzedni_przemiat)s
  AND (ends_at IS NULL OR ends_at > %(teraz)s)
"""

SQL_AUCTION_PO_KLUCZU = """
SELECT
    id, source_id, external_id, url, make, model, variant, year, mileage_km, fuel,
    gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color, location,
    seller, price_start,
    price_current, currency, bid_count, bid_increment_raw, ends_at, status,
    first_seen_at, last_seen_at, content_hash, raw_json, next_poll_at, poll_tier,
    consecutive_failures, final_price_state, last_price_lead_seconds, duplicate_of
FROM app.auction WHERE source_id = %s AND external_id = %s
"""

# Zapytanie wykonywane najczesciej w calym systemie — musi trafiac w indeks
# czesciowy auction_next_poll_active_idx (SPEC.md §8.3).
SQL_AUCTION_DO_ODPYTU = """
SELECT a.id, a.source_id, a.external_id, a.url, a.make, a.model, a.variant,
    a.year, a.mileage_km, a.fuel, a.gearbox, a.engine_ccm, a.engine_hp,
    a.vin, a.body, a.vehicle_kind, a.color, a.location, a.seller,
    a.price_start, a.price_current, a.currency, a.bid_count,
    a.bid_increment_raw, a.ends_at, a.status, a.first_seen_at,
    a.last_seen_at, a.content_hash, a.raw_json, a.next_poll_at, a.poll_tier,
    a.consecutive_failures, a.final_price_state, a.last_price_lead_seconds,
    a.duplicate_of
FROM app.auction AS a
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id
WHERE a.status = 'ACTIVE' AND a.next_poll_at IS NOT NULL AND a.next_poll_at <= %s
ORDER BY CASE
    WHEN a.poll_tier = 'CLOSING' THEN 0
    WHEN w.auction_id IS NOT NULL THEN 1
    ELSE 2
END, a.ends_at NULLS LAST
LIMIT %s
"""

SQL_AUCTION_NAJBLIZSZY_TERMIN = """
SELECT min(next_poll_at) FROM app.auction
WHERE status = 'ACTIVE' AND next_poll_at IS NOT NULL
"""

SQL_AUCTION_PO_VIN = """
SELECT
    id, source_id, external_id, url, make, model, variant, year, mileage_km, fuel,
    gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color, location,
    seller, price_start,
    price_current, currency, bid_count, bid_increment_raw, ends_at, status,
    first_seen_at, last_seen_at, content_hash, raw_json, next_poll_at, poll_tier,
    consecutive_failures, final_price_state, last_price_lead_seconds, duplicate_of
FROM app.auction WHERE vin = %s ORDER BY id
"""

SQL_AUCTION_ZAPLANUJ = """
UPDATE app.auction SET next_poll_at = %s, poll_tier = %s WHERE id = %s
"""

# Zamkniecie aukcji, ktora dawno minela swoj termin (SPEC.md §11.5, §12).
#
# Bez tego aukcja nieobserwowana zostaje `ACTIVE` NA ZAWSZE: odpytujemy ja
# raz i wiecej nie wracamy (§11.2), a marker konca (`auction_pending:false`
# w poleasingowe.pl) stoi wylacznie na stronie szczegolow. Skutek widoczny
# w interfejsie: zakonczone aukcje siedza w widoku „Aktywne".
#
# Karencja liczy sie PER ZRODLO z jego okna dogrywki: poleasingowe.pl
# przedluza aukcje maksymalnie o `overtime_cap_seconds` (1800 s), wiec przed
# uplywem tego czasu „po terminie" nie znaczy jeszcze „zakonczona". Gdy
# serwis nie deklaruje sufitu (EFL), bierzemy godzine.
#
# `final_price_state` schodzi na LAST_SEEN, nie CONFIRMED: ceny po zamknieciu
# nikt nie odczytal, wiec to dolne oszacowanie (§8.2). `last_price_lead_seconds`
# mowi, jak bardzo ostatni odczyt wyprzedzil koniec — im wiecej, tym mniej
# wart jest ten pomiar.
KARENCJA_ZAMKNIECIA_S = 600
"""Zapas na dryf zegara i na to, ze `ends_at` pochodzi z ostatniego
odpytu, a nie z tej sekundy (SPEC.md §11.7). Dziesiec minut to tez
mniej wiecej tyle, ile EFL sam potrzebuje na dopisanie "Zakonczona"
(5-7 min, RECON.md §4.1) — czyli nie zamykamy aukcji wczesniej, niz
zrobilby to serwis, gdybysmy go zapytali."""

SQL_AUCTION_ZAMKNIJ_PO_TERMINIE = """
UPDATE app.auction AS a
SET status = 'ENDED',
    next_poll_at = NULL,
    poll_tier = 'IDLE',
    final_price_state = CASE
        WHEN a.final_price_state = 'UNKNOWN' THEN 'LAST_SEEN'
        ELSE a.final_price_state
    END,
    last_price_lead_seconds = CASE
        WHEN a.final_price_state = 'UNKNOWN'
        THEN GREATEST(0, EXTRACT(EPOCH FROM (a.ends_at - a.last_seen_at)))::int
        ELSE a.last_price_lead_seconds
    END
FROM app.source AS s
WHERE s.id = a.source_id
  AND a.status = 'ACTIVE'
  AND a.ends_at IS NOT NULL
  AND a.ends_at < %(teraz)s - make_interval(secs => GREATEST(
      CASE
          -- Zrodlo Z DOGRYWKA: `ends_at` moze sie jeszcze przesunac, wiec
          -- czekamy caly mozliwy czas przedluzenia i dopiero potem uznajemy
          -- koniec.
          WHEN s.overtime_window_seconds > 0 AND s.overtime_extension_seconds > 0
          THEN COALESCE(s.overtime_cap_seconds, 3600) + %(karencja)s
          -- Zrodlo BEZ DOGRYWKI: `ends_at` jest twardy (EFL — RECON.md §3.2),
          -- wiec czekanie godziny "na wszelki wypadek" trzymalo zakonczone
          -- aukcje w widoku "Aktywne" bez zadnego powodu. Zostaje sama
          -- karencja.
          ELSE %(karencja)s
      END,
      -- Zegar NIE MA PRAWA wyprzedzic drabinki domkniecia. Zamkniecie aukcji
      -- w trakcie fazy 2 uciela by ostatnie stopnie — a to wlasnie one
      -- lapia potwierdzenie zakonczenia, od ktorego zalezy `CONFIRMED`
      -- (§11.5). Zapas 60 s na obrot petli dyspozytora.
      COALESCE((SELECT max(x) FROM unnest(s.closing_ladder_seconds) AS x), 0) + 60
  ))
"""
SQL_AUCTION_ODNOTUJ_WIDZIANA = "UPDATE app.auction SET last_seen_at = %s WHERE id = %s"

SQL_SNAPSHOT_OSTATNI = """
SELECT
    id, auction_id, ts, price, currency, bid_count, ends_at, bid_gap
FROM app.price_snapshot
WHERE auction_id = %s ORDER BY ts DESC, id DESC LIMIT 1
"""

SQL_SNAPSHOT_INSERT = """
INSERT INTO app.price_snapshot
    (auction_id, ts, price, currency, bid_count, ends_at, bid_gap)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id, auction_id, ts, price, currency, bid_count, ends_at, bid_gap
"""

SQL_SNAPSHOT_HISTORIA = """
SELECT
    id, auction_id, ts, price, currency, bid_count, ends_at, bid_gap
FROM app.price_snapshot
WHERE auction_id = %s ORDER BY ts, id
"""

SQL_OFERTA_INSERT = """
INSERT INTO app.offer
    (auction_id, uczestnik, amount, currency, placed_at, first_seen_at,
     external_offer_id)
VALUES (%s, %s, %s, %s, %s, %s, %s)
-- Bez wskazania konkretnego klucza: zrodla maja rozne. poleasingowe daje
-- wlasny identyfikator oferty (`013`), EFL nie daje zadnego i rozstrzyga
-- tam klucz naturalny z `012`. `DO NOTHING` bez celu lapie oba.
ON CONFLICT DO NOTHING
RETURNING id
"""

SQL_OFERTA_DLA_AUKCJI = """
SELECT id, auction_id, uczestnik, amount, currency, placed_at, first_seen_at,
       external_offer_id
FROM app.offer
WHERE auction_id = %s
ORDER BY placed_at, id
"""

SQL_WATCHLIST_DODAJ = """
INSERT INTO app.watchlist (auction_id, note, added_at)
VALUES (%s, %s, %s)
ON CONFLICT (auction_id) DO UPDATE SET
    note = COALESCE(EXCLUDED.note, app.watchlist.note)
RETURNING id, auction_id, note, added_at
"""
SQL_WATCHLIST_NOTATKA = """
UPDATE app.watchlist SET note = %s WHERE auction_id = %s
RETURNING id, auction_id, note, added_at
"""
SQL_WATCHLIST_USUN = "DELETE FROM app.watchlist WHERE auction_id = %s"
SQL_WATCHLIST_JEST = "SELECT 1 FROM app.watchlist WHERE auction_id = %s"
SQL_WATCHLIST_WPIS = """
SELECT id, auction_id, note, added_at
FROM app.watchlist WHERE auction_id = %s
"""

# Wycena AI — JEDEN wiersz na aukcje. `ON CONFLICT DO UPDATE`, bo "Przelicz"
# ma nadpisac poprzednia opinie, a nie odkladac kolejna: dwie wyceny tej samej
# aukcji roznilyby sie kwota i nie byloby wiadomo, ktora obowiazuje.
SQL_WYCENA_ZAPISZ = """
INSERT INTO app.ai_valuation (
    auction_id, value_amount, min_amount, max_amount, currency, portal_amount,
    confidence, rationale, assumptions, model, prompt_version, created_at
) VALUES (
    %(auction_id)s, %(value_amount)s, %(min_amount)s, %(max_amount)s,
    %(currency)s, %(portal_amount)s, %(confidence)s, %(rationale)s,
    %(assumptions)s, %(model)s, %(prompt_version)s, %(created_at)s
)
ON CONFLICT (auction_id) DO UPDATE SET
    value_amount = EXCLUDED.value_amount,
    min_amount = EXCLUDED.min_amount,
    max_amount = EXCLUDED.max_amount,
    currency = EXCLUDED.currency,
    portal_amount = EXCLUDED.portal_amount,
    confidence = EXCLUDED.confidence,
    rationale = EXCLUDED.rationale,
    assumptions = EXCLUDED.assumptions,
    model = EXCLUDED.model,
    prompt_version = EXCLUDED.prompt_version,
    created_at = EXCLUDED.created_at
RETURNING auction_id, value_amount, min_amount, max_amount, currency,
    portal_amount, confidence, rationale, assumptions, model, prompt_version,
    created_at
"""

SQL_WYCENA_DLA_AUKCJI = """
SELECT auction_id, value_amount, min_amount, max_amount, currency,
       portal_amount, confidence, rationale, assumptions, model,
       prompt_version, created_at
FROM app.ai_valuation WHERE auction_id = %s
"""

SQL_SAVED_FILTER_ZAPISZ = """
INSERT INTO app.saved_filter (name, criteria, created_at)
VALUES (%s, %s, %s)
ON CONFLICT (name) DO UPDATE SET criteria = EXCLUDED.criteria
RETURNING id, name, criteria, created_at
"""
SQL_SAVED_FILTER_WSZYSTKIE = """
SELECT id, name, criteria, created_at FROM app.saved_filter
ORDER BY name COLLATE "pl-PL-x-icu"
"""
SQL_SAVED_FILTER_USUN = "DELETE FROM app.saved_filter WHERE id = %s"

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
        # Postgres oddaje `integer[]` jako liste; encja jest frozen, wiec
        # trzyma krotke (SPEC.md §5 — encje niemutowalne).
        closing_ladder_seconds=tuple(w["closing_ladder_seconds"]),
        bid_history_ttl_seconds=w["bid_history_ttl_seconds"],
        bid_count_semantics=BidCountSemantics(w["bid_count_semantics"]),
        last_sweep_at=w["last_sweep_at"],
        last_sweep_attempt_at=w["last_sweep_attempt_at"],
        last_sweep_status=SweepStatus(w["last_sweep_status"]),
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
        vehicle_kind=RodzajPojazdu(w["vehicle_kind"]),
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


def _na_oferte(w: dict[str, Any]) -> OfertaUczestnika:
    return OfertaUczestnika(
        id=w["id"],
        auction_id=w["auction_id"],
        uczestnik=w["uczestnik"],
        amount=Money(w["amount"], Currency(w["currency"])),
        placed_at=w["placed_at"],
        first_seen_at=w["first_seen_at"],
        external_offer_id=w["external_offer_id"],
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
                    "closing_ladder_seconds": list(source.closing_ladder_seconds),
                    "bid_history_ttl_seconds": source.bid_history_ttl_seconds,
                    "bid_count_semantics": source.bid_count_semantics.value,
                    "last_sweep_at": source.last_sweep_at,
                    "last_sweep_attempt_at": source.last_sweep_attempt_at,
                    "last_sweep_status": source.last_sweep_status.value,
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
                    "vehicle_kind": auction.vehicle_kind.value,
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

    async def zapisz_z_przemiatu(self, aukcje: Sequence[Auction]) -> int:
        """Zbiorczy zapis pozycji z listy. Zwraca liczbę **nowych** aukcji.

        Jedna transakcja, `executemany` — nie wiersz po wierszu (SPEC.md §8.4).
        Przy ~721 pojazdach poleasingowe.pl różnica jest odczuwalna na tej
        maszynie, a wolumen zapisów ma pozostać znikomy wobec TeslaMate (§2).

        `xmax = 0` w `RETURNING` odróżnia wstawienie od aktualizacji — to
        jedyny sposób, żeby policzyć nowe aukcje bez dodatkowego zapytania.
        """
        if not aukcje:
            return 0
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.executemany(
                SQL_AUCTION_Z_PRZEMIATU,
                [self._parametry_przemiatu(a) for a in aukcje],
                returning=True,
            )
            nowe = 0
            while True:
                wiersz = await cur.fetchone()
                if wiersz is not None and wiersz["nowa"]:
                    nowe += 1
                if not cur.nextset():
                    break
        return nowe

    @staticmethod
    def _parametry_przemiatu(auction: Auction) -> dict[str, Any]:
        cena = auction.price_current or auction.price_start
        return {
            "source_id": auction.source_id,
            "external_id": auction.external_id,
            "url": auction.url,
            "make": auction.make,
            "model": auction.model,
            "variant": auction.variant,
            "year": auction.year,
            "mileage_km": None if auction.mileage is None else auction.mileage.km,
            "fuel": auction.fuel,
            "gearbox": auction.gearbox,
            "engine_ccm": auction.engine_ccm,
            "engine_hp": auction.engine_hp,
            "vin": None if auction.vin is None else auction.vin.value,
            "body": auction.body,
            "vehicle_kind": auction.vehicle_kind.value,
            "color": auction.color,
            "location": auction.location,
            "seller": auction.seller,
            "price_start": None
            if auction.price_start is None
            else auction.price_start.amount,
            "price_current": None
            if auction.price_current is None
            else auction.price_current.amount,
            "currency": (cena.currency if cena else Currency.PLN).value,
            "bid_count": auction.bid_count,
            "bid_increment_raw": auction.bid_increment_raw,
            "ends_at": auction.ends_at,
            "status": auction.status.value,
            "first_seen_at": auction.first_seen_at,
            "last_seen_at": auction.last_seen_at,
            # Tylko dla WSTAWIANYCH wierszy — `ON CONFLICT` tych dwóch kolumn
            # nie rusza, więc aukcja już obecna w bazie zachowuje swój termin.
            "next_poll_at": auction.next_poll_at,
            "poll_tier": auction.poll_tier.value,
        }

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

    async def najblizszy_termin(self) -> dt.datetime | None:
        """Najbliższy `next_poll_at` — do wyliczenia, jak długo spać (§11.1).

        Pętla śpi do najbliższego terminu, nie budzi się na stałym ticku.
        Bez tego zapytania „ile spać" byłoby zgadywaniem, a stały tick
        odpytywałby bazę bez powodu — na instancji dzielonej z TeslaMate.
        """
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_AUCTION_NAJBLIZSZY_TERMIN)
            wiersz = await cur.fetchone()
        if wiersz is None:
            return None
        termin: dt.datetime | None = wiersz[0]
        return termin

    async def po_vin(self, vin: Vin) -> Sequence[Auction]:
        """Podstawa deduplikacji między serwisami (SPEC.md §8.4)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_AUCTION_PO_VIN, (vin.value,))
            return [_na_auction(w) for w in await cur.fetchall()]

    async def zaplanuj(
        self, auction_id: int, next_poll_at: dt.datetime | None, poll_tier: PollTier
    ) -> None:
        """Włącza albo wyłącza pojedynczy odpyt tej aukcji (SPEC.md §11.2).

        `None` znaczy „nie odpytuj" — tak wygląda aukcja nieobserwowana,
        której wystarcza zbiorczy przemiat listy.
        """
        async with self._conn.cursor() as cur:
            await cur.execute(
                SQL_AUCTION_ZAPLANUJ, (next_poll_at, poll_tier.value, auction_id)
            )

    async def oznacz_zniknione(
        self, source_id: int, poprzedni_przemiat: dt.datetime, teraz: dt.datetime
    ) -> int:
        """Aukcje, których nie było w dwóch ostatnich przemiatach. Zwraca ile."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                SQL_AUCTION_OZNACZ_ZNIKNIETE,
                {
                    "source_id": source_id,
                    "poprzedni_przemiat": poprzedni_przemiat,
                    "teraz": teraz,
                },
            )
            return cur.rowcount

    async def zamknij_po_terminie(self, teraz: dt.datetime) -> int:
        """Zamyka aukcje, które dawno minęły termin. Zwraca ile.

        Jedno zapytanie na obrót dispatchera, bez pobierania wierszy do
        Pythona — to sprzątanie stanu, nie odczyt danych.

        Karencja zależy od tego, czy serwis **w ogóle ma dogrywkę**. Tam,
        gdzie ma, `ends_at` może się jeszcze przesunąć i trzeba odczekać
        cały możliwy czas przedłużenia. Tam, gdzie nie ma — EFL, gdzie
        `ends_at` jest twardy (RECON.md §3.2) — czekanie godziny „na wszelki
        wypadek" trzymało zakończone aukcje na szczycie widoku „Aktywne",
        bo lista jest domyślnie sortowana po najbliższym terminie.
        """
        async with self._conn.cursor() as cur:
            await cur.execute(
                SQL_AUCTION_ZAMKNIJ_PO_TERMINIE,
                {"teraz": teraz, "karencja": KARENCJA_ZAMKNIECIA_S},
            )
            return cur.rowcount

    async def odnotuj_widziana(self, auction_id: int, teraz: dt.datetime) -> None:
        """Odpyt bez zmiany aktualizuje tylko `last_seen_at` (SPEC.md §8.4)."""
        await self._conn.execute(SQL_AUCTION_ODNOTUJ_WIDZIANA, (teraz, auction_id))


class PgSnapshotRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz_jesli_zmienil_sie(
        self, snapshot: PriceSnapshot, *, licznik_liczy_oferty: bool = False
    ) -> PriceSnapshot | None:
        """Zapisuje snapshot **wyłącznie** przy zmianie (SPEC.md §8.4).

        Bez tej reguły dogrywka generuje setki identycznych wierszy na aukcję
        i niepotrzebnie obciąża instancję dzieloną z TeslaMate.

        Przy okazji wylicza `bid_gap` (SPEC.md §11.8): przyrost `bid_count`
        ponad 1, liczony względem **poprzedniego snapshotu tej aukcji**.
        Pierwszy snapshot dostaje `None`, nie `0` — w chwili pierwszej
        obserwacji aukcja mogła już mieć oferty.

        `licznik_liczy_oferty` przekazuje `Source.liczy_oferty`. Bez niego
        `bid_gap` liczył się dla **każdego** źródła, także dla EFL, gdzie
        `bid_count` to liczba uczestników licytacji proxy, a nie ofert:
        wychodziło stamtąd zero mimo realnych zmian ceny, czytane potem jak
        „komplet historii". §11.8 mówi wprost, że dla `PARTICIPANTS`
        i `UNKNOWN` właściwą odpowiedzią jest `NULL`.

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
            licznik_liczy_oferty
            and poprzedni is not None
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


class PgOfertaRepository:
    """Oferty odczytane wprost ze strony aukcji (SPEC.md §11.8)."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz_nowe(self, oferty: Sequence[OfertaUczestnika]) -> int:
        """Dopisuje tylko oferty, których jeszcze nie mamy.

        `DO NOTHING` na kluczu naturalnym, a nie sprawdzanie „czy już jest"
        w Pythonie: w fazie domknięcia ta sama lista wraca po kilka razy
        w ciągu kilkudziesięciu sekund (§11.5), a przy okazji ratuje nas to
        przed wyścigiem, gdyby kiedyś dwa odpyty tej aukcji nałożyły się
        na siebie.
        """
        nowe = 0
        async with self._conn.cursor() as cur:
            for oferta in oferty:
                await cur.execute(
                    SQL_OFERTA_INSERT,
                    (
                        oferta.auction_id,
                        oferta.uczestnik,
                        oferta.amount.amount,
                        oferta.amount.currency.value,
                        oferta.placed_at,
                        oferta.first_seen_at,
                        oferta.external_offer_id,
                    ),
                )
                if await cur.fetchone() is not None:
                    nowe += 1
        return nowe

    async def dla_aukcji(self, auction_id: int) -> Sequence[OfertaUczestnika]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_OFERTA_DLA_AUKCJI, (auction_id,))
            return [_na_oferte(w) for w in await cur.fetchall()]


class PgWatchlistRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def dodaj(self, wpis: WatchlistEntry) -> WatchlistEntry:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_WATCHLIST_DODAJ,
                (
                    wpis.auction_id,
                    wpis.note,
                    wpis.added_at,
                ),
            )
            w = await cur.fetchone()
        assert w is not None
        return WatchlistEntry(
            id=w["id"],
            auction_id=w["auction_id"],
            note=w["note"],
            added_at=w["added_at"],
        )

    async def zapisz_notatke(
        self, auction_id: int, note: str | None
    ) -> WatchlistEntry | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_WATCHLIST_NOTATKA, (note, auction_id))
            w = await cur.fetchone()
        if w is None:
            return None
        return WatchlistEntry(
            id=w["id"],
            auction_id=w["auction_id"],
            note=w["note"],
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

    async def wpis(self, auction_id: int) -> WatchlistEntry | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_WATCHLIST_WPIS, (auction_id,))
            w = await cur.fetchone()
        if w is None:
            return None
        return WatchlistEntry(
            id=w["id"],
            auction_id=w["auction_id"],
            note=w["note"],
            added_at=w["added_at"],
        )


def _na_wycene(w: dict[str, Any]) -> WycenaAukcji:
    waluta = Currency(w["currency"])
    return WycenaAukcji(
        auction_id=w["auction_id"],
        wartosc=Money(w["value_amount"], waluta),
        minimum=Money(w["min_amount"], waluta),
        maksimum=Money(w["max_amount"], waluta),
        cena_portale=(
            None if w["portal_amount"] is None else Money(w["portal_amount"], waluta)
        ),
        pewnosc=w["confidence"],
        uzasadnienie=w["rationale"],
        zalozenia=tuple(w["assumptions"] or ()),
        model=w["model"],
        wersja_promptu=w["prompt_version"],
        utworzono=w["created_at"],
    )


class PgWycenaRepository:
    """Trwała wycena AI (SPEC.md §12) — zapis, nie cache."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz(self, wycena: WycenaAukcji) -> WycenaAukcji:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_WYCENA_ZAPISZ,
                {
                    "auction_id": wycena.auction_id,
                    "value_amount": wycena.wartosc.amount,
                    "min_amount": wycena.minimum.amount,
                    "max_amount": wycena.maksimum.amount,
                    "currency": wycena.wartosc.currency.value,
                    "portal_amount": (
                        None
                        if wycena.cena_portale is None
                        else wycena.cena_portale.amount
                    ),
                    "confidence": wycena.pewnosc,
                    "rationale": wycena.uzasadnienie,
                    "assumptions": list(wycena.zalozenia),
                    "model": wycena.model,
                    "prompt_version": wycena.wersja_promptu,
                    "created_at": wycena.utworzono,
                },
            )
            w = await cur.fetchone()
        assert w is not None
        return _na_wycene(w)

    async def dla_aukcji(self, auction_id: int) -> WycenaAukcji | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_WYCENA_DLA_AUKCJI, (auction_id,))
            w = await cur.fetchone()
        return None if w is None else _na_wycene(w)


class PgSavedFilterRepository:
    """Zapisane filtry (SPEC.md §12). Kryteria jako `jsonb` (§8.2)."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def zapisz(self, wpis: SavedFilter) -> SavedFilter:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                SQL_SAVED_FILTER_ZAPISZ,
                (wpis.name, json.dumps(wpis.criteria), wpis.created_at),
            )
            w = await cur.fetchone()
        assert w is not None
        return SavedFilter(
            id=w["id"],
            name=w["name"],
            criteria=w["criteria"],
            created_at=w["created_at"],
        )

    async def wszystkie(self) -> Sequence[SavedFilter]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(SQL_SAVED_FILTER_WSZYSTKIE)
            wiersze = await cur.fetchall()
        return [
            SavedFilter(
                id=w["id"],
                name=w["name"],
                criteria=w["criteria"],
                created_at=w["created_at"],
            )
            for w in wiersze
        ]

    async def usun(self, filter_id: int) -> bool:
        async with self._conn.cursor() as cur:
            await cur.execute(SQL_SAVED_FILTER_USUN, (filter_id,))
            return cur.rowcount > 0


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
        # Adnotacje portami, nie klasami konkretnymi. Atrybuty protokolu sa
        # niezmiennicze, wiec bez tego `PgUnitOfWork` nie spelnialby portu
        # `UnitOfWork` mimo identycznego zachowania.
        self.source: SourceRepository = PgSourceRepository(conn)
        self.auction: AuctionRepository = PgAuctionRepository(conn)
        self.snapshot: SnapshotRepository = PgSnapshotRepository(conn)
        self.oferta: OfertaRepository = PgOfertaRepository(conn)
        self.watchlist: WatchlistRepository = PgWatchlistRepository(conn)
        self.saved_filter: SavedFilterRepository = PgSavedFilterRepository(conn)
        self.wycena: WycenaRepository = PgWycenaRepository(conn)
        self.run_log: RunLogRepository = PgRunLogRepository(conn)

    async def __aenter__(self) -> PgUnitOfWork:
        self._transakcja = self._conn.transaction()
        await self._transakcja.__aenter__()
        return self

    async def __aexit__(self, *wyjatek: Any) -> None:
        assert self._transakcja is not None
        await self._transakcja.__aexit__(*wyjatek)
        self._transakcja = None
