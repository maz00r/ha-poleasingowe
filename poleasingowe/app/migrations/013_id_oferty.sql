-- 013_id_oferty.sql — identyfikator oferty nadany przez serwis (SPEC.md §11.8).
--
-- `012` kluczowal oferty naturalnie: (aukcja, uczestnik, moment zlozenia).
-- Dla EFL innego wyboru nie bylo. poleasingowe.pl daje jednak WPROST staly
-- identyfikator kazdej oferty (`lastOffers[].id`), a to jest lepszy klucz
-- z dwoch powodow:
--
--   1. Nazwa licytanta jest tam zredagowana juz po stronie serwisu ("u...k"),
--      wiec kolumna `uczestnik` nie rozroznia licytantow i nie moze
--      wspoltworzyc klucza.
--   2. Dwie oferty w tej samej sekundzie zlalyby sie w jedna. Przy licytacji,
--      w ktorej postapienia padaja co kilka sekund, to nie jest przypadek
--      teoretyczny.
--
-- Kolumna jest NULLOWALNA, bo EFL zadnego identyfikatora oferty nie podaje —
-- tam nadal rozstrzyga klucz z `012`.

ALTER TABLE app.offer
    ADD COLUMN IF NOT EXISTS external_offer_id text;

-- Indeks czesciowy: wiersze bez identyfikatora (EFL) nie maja sie o siebie
-- rozbijac, bo dla nich unikalnosc pilnuje klucz naturalny z `012`.
CREATE UNIQUE INDEX IF NOT EXISTS offer_external_id_unique_idx
    ON app.offer (auction_id, external_offer_id)
    WHERE external_offer_id IS NOT NULL;

COMMENT ON COLUMN app.offer.external_offer_id IS
    'Identyfikator oferty nadany przez serwis, o ile go podaje. NULL dla '
    'zrodel bez takiego pola — wtedy rozstrzyga (auction_id, uczestnik, '
    'placed_at) z migracji 012.';
