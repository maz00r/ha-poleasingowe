-- Cena docelowa nie jest juz funkcja produktu. Notatka zostaje przy
-- obserwowanej aukcji, ale sama obserwacja ma byc jednym, lekkim przelacznikiem.
-- Widok raportowy zachowuje dawny naglowek, aby dashboardy nie przestaly sie
-- wykonywac; kolumna ma odtad zawsze NULL.
CREATE OR REPLACE VIEW reporting.v_auction_current AS
SELECT
    a.id AS auction_id,
    src.key AS source_key,
    src.name AS source_name,
    a.external_id,
    a.url,
    a.make,
    a.model,
    a.variant,
    a.year,
    a.mileage_km,
    a.fuel,
    a.gearbox,
    a.engine_ccm,
    a.engine_hp,
    a.vin,
    a.body,
    a.color,
    a.location,
    a.seller,
    a.price_start,
    a.price_current,
    a.currency,
    a.bid_count,
    a.bid_increment_raw,
    a.ends_at,
    a.status,
    a.final_price_state,
    a.last_price_lead_seconds,
    a.first_seen_at,
    a.last_seen_at,
    a.duplicate_of,
    (w.auction_id IS NOT NULL) AS watched,
    w.note AS watch_note,
    NULL::numeric AS watch_target_price
FROM app.auction AS a
JOIN app.source AS src ON src.id = a.source_id
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id;

ALTER TABLE app.watchlist
    DROP CONSTRAINT IF EXISTS watchlist_cena_nieujemna,
    DROP CONSTRAINT IF EXISTS watchlist_currency_check,
    DROP COLUMN target_price,
    DROP COLUMN currency;
