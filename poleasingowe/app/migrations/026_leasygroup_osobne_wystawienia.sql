-- Leasygroup rozroznia strone przedmiotu (28229 w adresie) od konkretnego
-- wystawienia (326766 w polu „Numer aukcji” i atrybucie data-id). Adapter
-- uzywal dotad pierwszej liczby jako klucza, przez co dwa wystawienia BMW
-- 750i zostaly polaczone w jedna historie. Rozdzielamy znany uszkodzony
-- rekord bez utraty odczytow, obserwacji ani czasu pierwszego zobaczenia.

DO $$
DECLARE
    stare_id bigint;
    nowe_id bigint;
    pierwszy_nowy timestamptz;
    stary_snapshot record;
    nowy_snapshot record;
BEGIN
    SELECT a.id
    INTO stare_id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'leasygroup'
      AND a.external_id = '28229'
      AND a.vin = 'WBA7R81020CK75026';

    IF stare_id IS NULL THEN
        RETURN;
    END IF;

    SELECT ps.ts, ps.price, ps.currency, ps.bid_count, ps.ends_at
    INTO stary_snapshot
    FROM app.price_snapshot AS ps
    WHERE ps.auction_id = stare_id
      AND ps.ends_at < timestamptz '2026-09-18 00:00:00+02'
    ORDER BY ps.ts DESC, ps.id DESC
    LIMIT 1;

    SELECT ps.ts, ps.price, ps.currency, ps.bid_count, ps.ends_at
    INTO nowy_snapshot
    FROM app.price_snapshot AS ps
    WHERE ps.auction_id = stare_id
      AND ps.ends_at >= timestamptz '2026-09-18 00:00:00+02'
    ORDER BY ps.ts DESC, ps.id DESC
    LIMIT 1;

    SELECT min(ps.ts)
    INTO pierwszy_nowy
    FROM app.price_snapshot AS ps
    WHERE ps.auction_id = stare_id
      AND ps.ends_at >= timestamptz '2026-09-18 00:00:00+02';

    IF stary_snapshot.ts IS NULL
       OR nowy_snapshot.ts IS NULL
       OR pierwszy_nowy IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO app.auction (
        source_id, external_id, url, make, model, variant, year, mileage_km,
        fuel, gearbox, engine_ccm, engine_hp, vin, body, vehicle_kind, color,
        location, seller, price_start, price_current, currency, bid_count,
        bid_increment_raw, ends_at, status, first_seen_at, last_seen_at,
        content_hash, raw_json, next_poll_at, poll_tier, consecutive_failures,
        final_price_state, last_price_lead_seconds, duplicate_of
    )
    SELECT
        a.source_id, '326766', a.url, a.make, a.model, a.variant, a.year,
        a.mileage_km, a.fuel, a.gearbox, a.engine_ccm, a.engine_hp, a.vin,
        a.body, a.vehicle_kind, a.color, a.location, a.seller,
        nowy_snapshot.price, nowy_snapshot.price, nowy_snapshot.currency,
        nowy_snapshot.bid_count, a.bid_increment_raw, nowy_snapshot.ends_at,
        CASE WHEN nowy_snapshot.ends_at > now() THEN 'ACTIVE' ELSE 'ENDED' END,
        pierwszy_nowy, GREATEST(pierwszy_nowy, a.last_seen_at),
        a.content_hash, a.raw_json,
        CASE WHEN nowy_snapshot.ends_at > now() THEN now() ELSE NULL END,
        CASE WHEN nowy_snapshot.ends_at > now() THEN 'FAR' ELSE 'IDLE' END,
        0,
        CASE WHEN nowy_snapshot.ends_at > now() THEN 'UNKNOWN' ELSE 'LAST_SEEN' END,
        NULL, NULL
    FROM app.auction AS a
    WHERE a.id = stare_id
    ON CONFLICT (source_id, external_id) DO NOTHING;

    SELECT a.id
    INTO nowe_id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'leasygroup'
      AND a.external_id = '326766';

    IF nowe_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE app.price_snapshot
    SET auction_id = nowe_id
    WHERE auction_id = stare_id
      AND ends_at >= timestamptz '2026-09-18 00:00:00+02';

    INSERT INTO app.watchlist (auction_id, note, added_at)
    SELECT nowe_id, w.note, w.added_at
    FROM app.watchlist AS w
    WHERE w.auction_id = stare_id
    ON CONFLICT (auction_id) DO UPDATE
    SET note = COALESCE(EXCLUDED.note, app.watchlist.note);

    -- Wycena utworzona juz podczas drugiego wystawienia dotyczy jego ceny
    -- i terminu. Przenosimy ja razem z tym wystawieniem, o ile nowa karta
    -- nie ma juz wlasnej wyceny.
    UPDATE app.ai_valuation AS v
    SET auction_id = nowe_id
    WHERE v.auction_id = stare_id
      AND v.created_at >= pierwszy_nowy
      AND NOT EXISTS (
          SELECT 1 FROM app.ai_valuation WHERE auction_id = nowe_id
      );

    UPDATE app.auction
    SET price_current = stary_snapshot.price,
        currency = stary_snapshot.currency,
        bid_count = stary_snapshot.bid_count,
        ends_at = stary_snapshot.ends_at,
        status = 'ENDED',
        last_seen_at = GREATEST(first_seen_at, stary_snapshot.ts),
        content_hash = NULL,
        raw_json = NULL,
        next_poll_at = NULL,
        poll_tier = 'IDLE',
        consecutive_failures = 0,
        final_price_state = 'LAST_SEEN',
        last_price_lead_seconds = GREATEST(
            0,
            EXTRACT(EPOCH FROM (stary_snapshot.ends_at - stary_snapshot.ts))::integer
        )
    WHERE id = stare_id;

    INSERT INTO app.photo_archive_state (auction_id, target, status)
    VALUES (
        nowe_id,
        CASE
            WHEN EXISTS (SELECT 1 FROM app.watchlist WHERE auction_id = nowe_id)
            THEN 'FULL'
            ELSE 'COVER'
        END,
        'PENDING'
    )
    ON CONFLICT (auction_id) DO NOTHING;

    -- Nowy klucz ma zostac zobaczony od razu po aktualizacji, bez czekania
    -- na kolejny szesciogodzinny interwal przemiatu.
    UPDATE app.source
    SET last_sweep_attempt_at = NULL
    WHERE key = 'leasygroup';
END
$$;
