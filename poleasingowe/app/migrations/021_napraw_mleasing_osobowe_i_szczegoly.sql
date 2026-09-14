-- Adapter mLeasing odczytuje wyłącznie `/oferty/osobowe/`. W starszej
-- wersji szczegóły gubiły tę deklarację i nadpisywały rodzaj zgadywaniem z
-- tytułu. Przywracamy właściwy rodzaj już zapisanych aukcji.
UPDATE app.auction AS a
SET vehicle_kind = 'OSOBOWY'
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'mleasing'
  AND a.status = 'ACTIVE';
