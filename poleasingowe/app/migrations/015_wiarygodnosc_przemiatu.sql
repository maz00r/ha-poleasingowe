-- 015_wiarygodnosc_przemiatu.sql — wynik i czas przemiatu listy.
--
-- Poprzednie `last_sweep_at` znaczyło tylko, że dispatcher podjął próbę.
-- Nie rozróżniało pełnej listy od timeoutu po pierwszej stronie, więc dwa
-- przerwane przebiegi mogły błędnie oznaczyć żywą aukcję jako DISAPPEARED.
-- Historycznych wpisów nie uznajemy za potwierdzony pełny przebieg.

ALTER TABLE app.source
    ADD COLUMN IF NOT EXISTS last_sweep_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_sweep_status text NOT NULL DEFAULT 'UNKNOWN';

ALTER TABLE app.source
    DROP CONSTRAINT IF EXISTS source_last_sweep_status_check;

ALTER TABLE app.source
    ADD CONSTRAINT source_last_sweep_status_check
    CHECK (last_sweep_status IN ('UNKNOWN', 'COMPLETE', 'PARTIAL', 'FAILED'));

COMMENT ON COLUMN app.source.last_sweep_at IS
    'Kiedy zakończył się ostatni pełny przemiat listy. Historyczne wartości '
    'mają status UNKNOWN i nie służą do oznaczania zniknięć.';
COMMENT ON COLUMN app.source.last_sweep_attempt_at IS
    'Kiedy rozpoczęto ostatnią próbę przemiatu, także częściową lub nieudaną.';
COMMENT ON COLUMN app.source.last_sweep_status IS
    'Wynik ostatniej próby: COMPLETE, PARTIAL, FAILED lub UNKNOWN dla danych '
    'sprzed migracji.';

-- Ostatni przebieg mógł być udany albo nie; nie wolno z niego wyciągać
-- wniosku o kompletności. Zachowujemy go wyłącznie jako czas ostatniej próby.
UPDATE app.source
SET last_sweep_attempt_at = COALESCE(last_sweep_attempt_at, last_sweep_at),
    last_sweep_status = 'UNKNOWN';

-- Dodatkowe wskaźniki jakości są dopisane na końcu istniejącego kontraktu.
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
    percentile_cont(0.5) WITHIN GROUP (ORDER BY last_observed_price)
        FILTER (WHERE final_price_state = 'CONFIRMED') AS median_confirmed,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY last_observed_price)
        FILTER (WHERE final_price_state = 'LAST_SEEN') AS median_last_seen,
    count(*) FILTER (WHERE final_price_state = 'CONFIRMED') AS n_confirmed,
    count(*) FILTER (WHERE final_price_state = 'LAST_SEEN') AS n_last_seen,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY last_price_lead_seconds)
        FILTER (WHERE last_price_lead_seconds IS NOT NULL) AS median_lead_seconds,
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY last_observed_price / NULLIF(price_start, 0)
    ) FILTER (WHERE price_start IS NOT NULL AND price_start > 0)
        AS median_stosunek_do_wywolawczej,
    count(*) AS n_total,
    count(*) FILTER (WHERE final_price_state = 'CONFIRMED')::numeric
        / NULLIF(count(*), 0) AS confirmed_share,
    count(*) FILTER (WHERE final_price_state = 'LAST_SEEN')::numeric
        / NULLIF(count(*), 0) AS last_seen_share
FROM zakonczone
GROUP BY make, model, year;

GRANT SELECT ON reporting.v_market_stats TO grafana_ro;

-- Widok jest kontraktem publicznym: nowe kolumny są dopisane na końcu.
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
    licznik.aktywne_aukcje,
    src.closing_ladder_seconds,
    src.bid_history_ttl_seconds,
    src.bid_count_semantics,
    src.last_sweep_attempt_at,
    src.last_sweep_at AS last_complete_sweep_at,
    src.last_sweep_status,
    EXTRACT(EPOCH FROM (ostatni.finished_at - ostatni.started_at))::double precision
        AS last_run_duration_seconds
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

GRANT SELECT ON reporting.v_source_health TO grafana_ro;
