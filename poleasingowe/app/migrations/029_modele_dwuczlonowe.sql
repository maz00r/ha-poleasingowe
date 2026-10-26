-- 029 — modele z dwóch słów („Model Y", „Seria 5", „Klasa C", 0.31.2).
--
-- Podział tytułu brał pierwsze słowo po marce jako model, więc Tesla dostawała
-- model „Model" (a „Y Performance" lądowało w wersji). Filtr modeli mieszał
-- wszystkie Tesle, a wycena z Otomoto szukała porównań po złym modelu.
-- `marki.podziel_marke_model` skleja teraz takie modele; ta migracja robi
-- to samo z wierszami już zebranymi. Drugi człon wersalikami, jak w kodzie.

UPDATE app.auction
SET model = initcap(model) || ' ' || upper(split_part(variant, ' ', 1)),
    variant = NULLIF(trim(substr(variant, length(split_part(variant, ' ', 1)) + 1)), '')
WHERE lower(model) IN ('model', 'seria', 'klasa', 'series', 'class')
  AND variant IS NOT NULL
  AND split_part(variant, ' ', 1) <> '';
