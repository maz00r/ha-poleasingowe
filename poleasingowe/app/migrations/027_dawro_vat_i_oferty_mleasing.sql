-- 027 — kwoty dawro netto, historia cen dawro i mLeasing (0.31.0).
--
-- 1. dawro publikuje kwoty BRUTTO. Od 0.31 mapper zapisuje netto (÷1,23)
--    dla faktury VAT, a umowę kupna-sprzedaży zostawia bez zmian. Nowa
--    kolumna mówi, którą regułę zastosowano — karta pokazuje to wprost.
--    Wierszy zebranych wcześniej nie da się przeliczyć w SQL-u: formy
--    sprzedaży nie zapisywaliśmy. Dostają `BRUTTO` (jak w serwisie,
--    forma nieznana), a aktywne zostaną odczytane od nowa — najbliższy
--    odczyt zapisze netto i właściwą informację.
-- 2. mLeasing od 0.31 czyta pełną listę ofert z API portalu, a dawro —
--    najwyższą ofertę przy każdym odczycie szczegółów. Wyczyszczenie
--    odcisku wymusza taki odczyt od razu, zamiast czekać na zmianę treści.

ALTER TABLE app.auction
    ADD COLUMN IF NOT EXISTS vat_info text;

ALTER TABLE app.auction
    DROP CONSTRAINT IF EXISTS auction_vat_info_check;
ALTER TABLE app.auction
    ADD CONSTRAINT auction_vat_info_check
    CHECK (vat_info IN ('PRZELICZONO_Z_BRUTTO', 'BEZ_VAT', 'BRUTTO'));

UPDATE app.auction AS a
SET vat_info = 'BRUTTO'
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'dawro'
  AND a.vat_info IS NULL;

UPDATE app.auction AS a
SET content_hash = NULL,
    next_poll_at = now()
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key IN ('dawro', 'mleasing')
  AND a.status = 'ACTIVE'
  AND (a.ends_at IS NULL OR a.ends_at > now());
