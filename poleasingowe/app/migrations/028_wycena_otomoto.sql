-- 028 — wycena z ogłoszeniami Otomoto i ekspertyzą (0.31.0).
--
-- Wycena uruchamiana przyciskiem „Wycena z Otomoto" zapisuje się w tym samym
-- wierszu co zwykła (jedna wycena na aukcję), ale niesie zmierzone dane
-- rynkowe: liczbę porównywalnych ogłoszeń, medianę i kwartyle cen
-- ofertowych, adres wyszukiwania i próbkę ogłoszeń. `portal_amount` jest
-- wtedy medianą z Otomoto, a nie szacunkiem modelu.

ALTER TABLE app.ai_valuation
    ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'LOKALNA',
    ADD COLUMN IF NOT EXISTS otomoto_count integer,
    ADD COLUMN IF NOT EXISTS otomoto_p25 numeric(12,2),
    ADD COLUMN IF NOT EXISTS otomoto_p75 numeric(12,2),
    ADD COLUMN IF NOT EXISTS otomoto_search_url text,
    ADD COLUMN IF NOT EXISTS otomoto_listings jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS appraisal_documents text[] NOT NULL DEFAULT '{}';

ALTER TABLE app.ai_valuation
    DROP CONSTRAINT IF EXISTS ai_valuation_source_kind_check;
ALTER TABLE app.ai_valuation
    ADD CONSTRAINT ai_valuation_source_kind_check
    CHECK (source_kind IN ('LOKALNA', 'OTOMOTO'));
