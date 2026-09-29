-- Dwa pierwsze odczyty BMW 750i byly osobnymi wystawieniami. Migracja 026
-- oddzielila od nich tylko biezace wystawienie 326766, pozostawiajac odczyty
-- z 10 i 14 wrzesnia na jednej karcie. Rozdzielamy rowniez te dwa historyczne
-- wystawienia. Numer konkretnego wystawienia z 10 wrzesnia nie zachowal sie
-- w starych danych, dlatego nadajemy jednoznaczny klucz migracyjny zawierajacy
-- dawny identyfikator strony i date pierwszego odczytu.

DO $$
DECLARE
    drugie_id bigint;
    pierwsze_id bigint;
    pierwszy_snapshot record;
    drugi_snapshot record;
BEGIN
    SELECT a.id
    INTO drugie_id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'leasygroup'
      AND a.external_id = '28229'
      AND a.vin = 'WBA7R81020CK75026';

    IF drugie_id IS NULL THEN
        RETURN;
    END IF;

    SELECT ps.id, ps.ts, ps.price, ps.currency, ps.bid_count, ps.ends_at
    INTO pierwszy_snapshot
    FROM app.price_snapshot AS ps
    WHERE ps.auction_id = drugie_id
      AND ps.ts = timestamptz '2026-09-10 20:01:57+00'
      AND ps.ends_at = timestamptz '2026-09-17 09:59:57+00'
    LIMIT 1;

    SELECT ps.ts, ps.price, ps.currency, ps.bid_count, ps.ends_at
    INTO drugi_snapshot
    FROM app.price_snapshot AS ps
    WHERE ps.auction_id = drugie_id
      AND ps.ts = timestamptz '2026-09-14 14:05:59+00'
      AND ps.ends_at = timestamptz '2026-09-17 09:59:59+00'
    LIMIT 1;

    IF pierwszy_snapshot.id IS NULL OR drugi_snapshot.ts IS NULL THEN
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
        a.source_id, '28229@2026-09-10', a.url, a.make, a.model, a.variant,
        a.year, a.mileage_km, a.fuel, a.gearbox, a.engine_ccm, a.engine_hp,
        a.vin, a.body, a.vehicle_kind, a.color, a.location, a.seller,
        pierwszy_snapshot.price, pierwszy_snapshot.price,
        pierwszy_snapshot.currency, pierwszy_snapshot.bid_count,
        a.bid_increment_raw, pierwszy_snapshot.ends_at, 'ENDED',
        pierwszy_snapshot.ts, pierwszy_snapshot.ts, NULL, NULL, NULL, 'IDLE',
        0, 'LAST_SEEN',
        GREATEST(
            0,
            EXTRACT(EPOCH FROM (
                pierwszy_snapshot.ends_at - pierwszy_snapshot.ts
            ))::integer
        ),
        NULL
    FROM app.auction AS a
    WHERE a.id = drugie_id
    ON CONFLICT (source_id, external_id) DO NOTHING;

    SELECT a.id
    INTO pierwsze_id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'leasygroup'
      AND a.external_id = '28229@2026-09-10';

    IF pierwsze_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE app.price_snapshot
    SET auction_id = pierwsze_id
    WHERE id = pierwszy_snapshot.id;

    INSERT INTO app.watchlist (auction_id, note, added_at)
    SELECT pierwsze_id, w.note, w.added_at
    FROM app.watchlist AS w
    WHERE w.auction_id = drugie_id
    ON CONFLICT (auction_id) DO UPDATE
    SET note = COALESCE(EXCLUDED.note, app.watchlist.note);

    UPDATE app.auction
    SET price_start = drugi_snapshot.price,
        price_current = drugi_snapshot.price,
        currency = drugi_snapshot.currency,
        bid_count = drugi_snapshot.bid_count,
        ends_at = drugi_snapshot.ends_at,
        status = 'ENDED',
        first_seen_at = drugi_snapshot.ts,
        last_seen_at = drugi_snapshot.ts,
        content_hash = NULL,
        raw_json = NULL,
        next_poll_at = NULL,
        poll_tier = 'IDLE',
        consecutive_failures = 0,
        final_price_state = 'LAST_SEEN',
        last_price_lead_seconds = GREATEST(
            0,
            EXTRACT(EPOCH FROM (
                drugi_snapshot.ends_at - drugi_snapshot.ts
            ))::integer
        )
    WHERE id = drugie_id;

    INSERT INTO app.photo_archive_state (auction_id, target, status)
    VALUES (
        pierwsze_id,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM app.watchlist WHERE auction_id = pierwsze_id
            )
            THEN 'FULL'
            ELSE 'COVER'
        END,
        'PENDING'
    )
    ON CONFLICT (auction_id) DO NOTHING;
END
$$;
