-- 002_reporting.sql — widoki w schemacie `reporting` dla Grafany (SPEC.md §9).
--
-- Widoki sa KONTRAKTEM PUBLICZNYM. Grafana nigdy nie odpytuje tabel bazowych:
-- gdyby dashboardy podpiely sie pod app.auction, schemat bylby zamrozony
-- i kazda migracja po cichu psulaby wykresy. Tabele bazowe wolno refaktorowac
-- dowolnie, dopoki widoki zwracaja to samo.
--
-- Schemat `reporting` juz istnieje (SPEC.md §0) — migracja go nie tworzy.

-- Rola Grafany musi istniec. Na serwerze docelowym istnieje (SPEC.md §0).
-- Jesli nie istnieje, wolimy glosny blad niz po cichu pominiety GRANT.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        RAISE EXCEPTION
            'Rola grafana_ro nie istnieje. Migracja 002 nadaje jej dostep do '
            'widokow (SPEC.md §9) i bez niej nie ma sensu. Utworz role poza '
            'add-onem — add-on nie zarzadza rolami.';
    END IF;
END
$$;

-- Idempotentnie, zgodnie z SPEC.md §9.
GRANT USAGE ON SCHEMA reporting TO grafana_ro;


-- v_price_history — pelna historia cen jednej aukcji.
CREATE OR REPLACE VIEW reporting.v_price_history AS
SELECT
    s.auction_id,
    s.ts,
    s.price,
    s.currency,
    s.bid_count,
    s.ends_at,
    s.bid_gap
FROM app.price_snapshot AS s;

COMMENT ON VIEW reporting.v_price_history IS
    'Historia cen aukcji. bid_gap mowi, ile ofert przegapiono przed danym '
    'snapshotem (SPEC.md §11.8); NULL oznacza brak wiedzy, nie komplet.';


-- v_auction_current — biezacy stan aukcji plus flaga obserwowana.
CREATE OR REPLACE VIEW reporting.v_auction_current AS
SELECT
    a.id AS auction_id,
    src.key            AS source_key,
    src.name           AS source_name,
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
    w.note                     AS watch_note,
    w.target_price             AS watch_target_price
FROM app.auction AS a
JOIN app.source  AS src ON src.id = a.source_id
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id;

COMMENT ON VIEW reporting.v_auction_current IS
    'Biezacy stan aukcji z flaga obserwowania. duplicate_of wskazuje aukcje '
    'uznana za te sama po VIN (SPEC.md §8.4) — do odsiania w agregatach.';


-- v_market_stats — statystyki rynkowe.
--
-- SPEC.md §9: kolumna ceny nazywa sie last_observed_price, NIE final_price,
-- bo cena zaobserwowana jako ostatnia nie zawsze jest cena koncowa.
-- Mediany CONFIRMED i LAST_SEEN sa LICZONE OSOBNO i nigdy mieszane:
-- LAST_SEEN to dolne oszacowanie (§11.5), wiec wrzucone do jednej mediany
-- zanizaloby obraz rynku. median_lead_seconds mowi, o ile ten pomiar
-- jest gorszy.
CREATE OR REPLACE VIEW reporting.v_market_stats AS
WITH zakonczone AS (
    SELECT
        a.make,
        a.model,
        a.year,
        a.final_price_state,
        a.price_start,
        a.price_current AS last_observed_price,
        a.last_price_lead_seconds
    FROM app.auction AS a
    WHERE a.final_price_state IN ('CONFIRMED', 'LAST_SEEN')
      AND a.price_current IS NOT NULL
      AND a.duplicate_of IS NULL
)
SELECT
    make,
    model,
    year,

    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY last_observed_price
    ) FILTER (WHERE final_price_state = 'CONFIRMED')   AS median_confirmed,

    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY last_observed_price
    ) FILTER (WHERE final_price_state = 'LAST_SEEN')   AS median_last_seen,

    count(*) FILTER (WHERE final_price_state = 'CONFIRMED') AS n_confirmed,
    count(*) FILTER (WHERE final_price_state = 'LAST_SEEN') AS n_last_seen,

    -- last_price_lead_seconds jest NULL dla CONFIRMED (§8.2), wiec ta mediana
    -- z natury opisuje wylacznie pomiary LAST_SEEN.
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY last_price_lead_seconds
    ) FILTER (WHERE last_price_lead_seconds IS NOT NULL) AS median_lead_seconds,

    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY last_observed_price / NULLIF(price_start, 0)
    ) FILTER (WHERE price_start IS NOT NULL AND price_start > 0)
        AS median_stosunek_do_wywolawczej
FROM zakonczone
GROUP BY make, model, year;

COMMENT ON VIEW reporting.v_market_stats IS
    'Statystyki rynkowe. Mediany CONFIRMED i LAST_SEEN OSOBNO — nigdy mieszane '
    '(SPEC.md §9). LAST_SEEN to dolne oszacowanie, a median_lead_seconds mowi, '
    'jak daleko przed koncem urwal sie pomiar. Duplikaty po VIN odsiane.';


-- v_source_health — stan zrodel i ostatnie przebiegi.
CREATE OR REPLACE VIEW reporting.v_source_health AS
SELECT
    src.id   AS source_id,
    src.key  AS source_key,
    src.name AS source_name,
    src.enabled,
    src.auth_state,
    src.consecutive_auth_failures,
    src.rate_limit_per_minute,
    src.floor_seconds,
    src.overtime_window_seconds,
    src.overtime_extension_seconds,
    src.overtime_cap_seconds,
    ostatni.started_at      AS last_run_started_at,
    ostatni.finished_at     AS last_run_finished_at,
    ostatni.new_count       AS last_run_new,
    ostatni.changed_count   AS last_run_changed,
    ostatni.error_count     AS last_run_errors,
    ostatni.rss_bytes       AS last_run_rss_bytes,
    ostatni.database_bytes  AS last_run_database_bytes,
    licznik.aktywne_aukcje
FROM app.source AS src
LEFT JOIN LATERAL (
    SELECT r.started_at, r.finished_at, r.new_count, r.changed_count,
           r.error_count, r.rss_bytes, r.database_bytes
    FROM app.run_log AS r
    WHERE r.source_id = src.id
    ORDER BY r.started_at DESC
    LIMIT 1
) AS ostatni ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS aktywne_aukcje
    FROM app.auction AS a
    WHERE a.source_id = src.id AND a.status = 'ACTIVE'
) AS licznik ON true;

COMMENT ON VIEW reporting.v_source_health IS
    'Stan zrodel: uwierzytelnienie, parametry dogrywki i ostatni przebieg '
    'z RSS oraz rozmiarem bazy — budzet z SPEC.md §1.1 ma byc mierzalny (§13).';


-- SPEC.md §9: ALTER DEFAULT PRIVILEGES jest juz ustawione na serwerze
-- docelowym, ale GRANT nadajemy JAWNIE — jest idempotentny, a nie chcemy
-- zalezec od stanu ustawionego recznie miesiace wczesniej.
GRANT SELECT ON reporting.v_price_history   TO grafana_ro;
GRANT SELECT ON reporting.v_auction_current TO grafana_ro;
GRANT SELECT ON reporting.v_market_stats    TO grafana_ro;
GRANT SELECT ON reporting.v_source_health   TO grafana_ro;
