-- Ogólne kolekcje „najnowsze” i „promowane” mLeasing nie deklarują
-- kategorii. W wersjach 0.29.1–0.29.6 trafiły stamtąd do bazy analizatory,
-- wózki i inne przedmioty. Nie są ofertami samochodów ani dostawczaków,
-- więc usuwamy je razem z zależną historią i obserwacjami. Prawidłowe
-- samochody mają przy zapisie rodzaj OSOBOWY albo DOSTAWCZY.

UPDATE app.auction
SET duplicate_of = NULL
WHERE duplicate_of IN (
    SELECT a.id
    FROM app.auction AS a
    JOIN app.source AS s ON s.id = a.source_id
    WHERE s.key = 'mleasing'
      AND a.vehicle_kind NOT IN ('OSOBOWY', 'DOSTAWCZY')
);

DELETE FROM app.auction AS a
USING app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'mleasing'
  AND a.vehicle_kind NOT IN ('OSOBOWY', 'DOSTAWCZY');
