-- 003_domkniecie.sql — parametry domkniecia aukcji per zrodlo (SPEC.md §11.5, §11.8).
--
-- ETAP 0b zmierzyl to, co 001 musialo zalozyc. Trzy wyniki, ktore wymuszaja
-- te kolumny (RECON.md §3.5 i §3.6):
--
-- 1. Okno widocznosci ceny po koncu rozjezdza sie o trzy rzedy wielkosci:
--    autoprzetarg 10-15 s (potem 302 na `/`), EFL i poleasingowe bez limitu.
--    Jedna stala drabinka fazy 2 nie moze pasowac do obu.
-- 2. W poleasingowe cena trzyma sie bezterminowo, ale historia ofert
--    (lastOffers) czyszczona jest 2-5 min po koncu. To DRUGI, wczesniejszy
--    deadline w tej samej fazie.
-- 3. EFL prowadzi licytacje proxy i pokazuje jeden wiersz na UCZESTNIKA,
--    aktualizowany w miejscu. bid_count nie liczy tam ofert, wiec bid_gap
--    z §11.8 mierzylby cos innego, niz obiecuje nazwa.
--
-- Domyslne wartosci sa celowo ostrozne: drabinka jak w pierwotnym §11.5,
-- semantyka 'UNKNOWN'. Zrodlo zmierzone dostaje swoje liczby przy rejestracji
-- (§14 pkt 5) — domyslna drabinka dla zrodla zmierzonego to blad konfiguracji,
-- nie ostroznosc.

ALTER TABLE app.source
    ADD COLUMN IF NOT EXISTS closing_ladder_seconds  integer[] NOT NULL
        DEFAULT '{2,5,10,20,40}',
    ADD COLUMN IF NOT EXISTS bid_history_ttl_seconds integer,
    ADD COLUMN IF NOT EXISTS bid_count_semantics     text      NOT NULL
        DEFAULT 'UNKNOWN';

-- Drabinka musi byc rosnaca i dodatnia. Bez tego "kolejny element" z §11.5
-- pkt 5 nie ma sensu, a kolejnosc prob zalezalaby od kolejnosci wpisu.
--
-- CHECK nie przyjmuje podzapytan ani funkcji zwracajacych zbiory, wiec
-- unnest() i ARRAY(SELECT ...) odpadaja. Stad funkcja pomocnicza: petla po
-- indeksach, bez SQL-a w srodku, wiec uczciwie IMMUTABLE.
CREATE OR REPLACE FUNCTION app.drabinka_poprawna(drabinka integer[])
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    i integer;
BEGIN
    IF drabinka IS NULL THEN
        RETURN false;
    END IF;
    -- Pusta tablica przechodzi. Uwaga: array_ndims('{}') to NULL, nie 0,
    -- wiec sprawdzenie wymiaru musi isc PO tym warunku, nie przed nim.
    IF cardinality(drabinka) = 0 THEN
        RETURN true;
    END IF;
    IF array_ndims(drabinka) IS DISTINCT FROM 1 THEN
        RETURN false;
    END IF;
    FOR i IN array_lower(drabinka, 1) .. array_upper(drabinka, 1) LOOP
        IF drabinka[i] IS NULL OR drabinka[i] <= 0 THEN
            RETURN false;
        END IF;
        IF i > array_lower(drabinka, 1) AND drabinka[i] <= drabinka[i - 1] THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

COMMENT ON FUNCTION app.drabinka_poprawna(integer[]) IS
    'Czy siatka prob fazy 2 jest rosnaca i dodatnia (SPEC.md §11.5). Pusta '
    'tablica przechodzi: znaczy "jeden odpyt i koniec", a nie blad.';

ALTER TABLE app.source
    DROP CONSTRAINT IF EXISTS source_drabinka_rosnaca;
ALTER TABLE app.source
    ADD CONSTRAINT source_drabinka_rosnaca
        CHECK (app.drabinka_poprawna(closing_ladder_seconds));

ALTER TABLE app.source
    DROP CONSTRAINT IF EXISTS source_bid_count_semantics_check;
ALTER TABLE app.source
    ADD CONSTRAINT source_bid_count_semantics_check
        CHECK (bid_count_semantics IN ('OFFERS', 'PARTICIPANTS', 'UNKNOWN'));

ALTER TABLE app.source
    DROP CONSTRAINT IF EXISTS source_bid_history_ttl_dodatni;
ALTER TABLE app.source
    ADD CONSTRAINT source_bid_history_ttl_dodatni
        CHECK (bid_history_ttl_seconds IS NULL OR bid_history_ttl_seconds > 0);

COMMENT ON COLUMN app.source.closing_ladder_seconds IS
    'Bezwzgledne przesuniecia prob fazy 2 od punktu zerowego, rosnace '
    '(SPEC.md §11.5). Bezwzgledne, nie odstepy — porownuja sie wprost ze '
    'zmierzonym oknem widocznosci ceny. Zmierzone: autoprzetarg {2,5,8,11,14}, '
    'EFL i poleasingowe {2,30}.';
COMMENT ON COLUMN app.source.bid_history_ttl_seconds IS
    'Jak dlugo po koncu widoczna jest historia ofert. NULL = nie znika. '
    'poleasingowe: ~120 s (lastOffers czyszczone 2-5 min po koncu). Ogon '
    'historii trzeba zebrac w tym oknie, nawet gdy cena jest juz CONFIRMED.';
COMMENT ON COLUMN app.source.bid_count_semantics IS
    'Co serwis liczy w bid_count. OFFERS = oferty, wtedy bid_gap ma sens. '
    'PARTICIPANTS = uczestnicy licytacji proxy (EFL) — bid_gap zostaje NULL. '
    'UNKNOWN dopoki rekonesans nie da dowodu; domysl, nie zalozenie.';


-- v_source_health pokazuje parametry dogrywki, wiec ma pokazywac takze
-- parametry domkniecia — inaczej z panelu nie da sie odpowiedziec, dlaczego
-- zrodlo ma taka a nie inna drabinke (SPEC.md §9, §12).
--
-- CREATE OR REPLACE VIEW pozwala tylko DOPISAC kolumny na koncu, wiec nowe
-- ida za aktywne_aukcje. Dashboardow jeszcze nie ma (§14 pkt 11), wiec ta
-- zmiana ksztaltu widoku jest teraz darmowa, a pozniej nie byla by.
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
    src.bid_count_semantics
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
    'Stan zrodel: uwierzytelnienie, parametry dogrywki i domkniecia, oraz '
    'ostatni przebieg z RSS i rozmiarem bazy (SPEC.md §1.1, §13).';

GRANT SELECT ON reporting.v_source_health TO grafana_ro;
