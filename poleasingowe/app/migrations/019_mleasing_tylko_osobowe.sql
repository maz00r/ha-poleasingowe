-- Zakres źródła mLeasing został doprecyzowany do samochodów osobowych.
-- Dostawcze pobrane w 0.29.8 nie powinny zajmować miejsca w tym katalogu.
UPDATE app.auction
SET duplicate_of = NULL
WHERE duplicate_of IN (
    SELECT a.id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'mleasing'
      AND a.vehicle_kind <> 'OSOBOWY'
);

DELETE FROM app.auction AS a
USING app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'mleasing'
  AND a.vehicle_kind <> 'OSOBOWY';

UPDATE app.source
SET last_sweep_at = NULL,
    last_sweep_attempt_at = NULL,
    last_sweep_status = 'UNKNOWN'
WHERE key = 'mleasing';
