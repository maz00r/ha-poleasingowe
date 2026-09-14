-- 024_marki_porzadek.sql — porządek w markach (SPEC.md §6.2, §9).
--
-- Po 006 filtr marek dalej rósł od danych: `Mercedes-Benz` obok
-- `Mercedes- Benz` (Leasygroup wstawia spację po myślniku), `Mini` zamiast
-- `MINI` z modelem `[BMW]` (dawro: „MINI [BMW] Countryman"), oraz marka
-- `Aukcja` — poleasingowe.pl po zakończeniu zmienia tytuł na „Aukcja nr …
-- zakończyła się …", a mapper brał z niego pierwsze słowo.
--
-- Od 0.29.14 `marki.py` porównuje po kluczu: bez nawiasów, myślnik bez
-- spacji wokół, jedna spacja, małe litery — i odrzuca tytuły, które nie są
-- nazwą pojazdu. Ta migracja robi to samo z wierszami JUŻ ZEBRANYMI, także
-- zakończonymi (te niosą ceny końcowe, §8.4).
--
-- UWAGA NA ROZJAZD: listy skrótów, aliasów i słów-nie-marek są tu
-- POWTÓRZONE za kodem. Pilnuje tego `test_marki.py`, który czyta ten plik.

-- 1. Tytuł opisujący aukcję, nie pojazd: nie ma marki, modelu ani wersji
--    (model i wersja to wtedy „nr" i „1384/STR/AU/2026 zakończyła się …").
UPDATE app.auction
SET make = NULL, model = NULL, variant = NULL
WHERE make IS NOT NULL
  AND lower(split_part(trim(make), ' ', 1)) = ANY (
      ARRAY['aukcja','licytacja','oferta','sprzedaz','sprzedaż']
  );

-- 2. Postać kanoniczna z klucza porównawczego.
UPDATE app.auction AS a
SET make = CASE
    WHEN alias.kanoniczna IS NOT NULL THEN alias.kanoniczna
    WHEN upper(k.klucz) = ANY (
        ARRAY['BMW','MAN','DAF','MG','DS','SEAT','KTM','JCB','BYD','FAW','GMC','RAM','MINI']
    ) THEN upper(k.klucz)
    ELSE initcap(k.klucz)
END
FROM (
    SELECT id,
           lower(trim(regexp_replace(regexp_replace(regexp_replace(
               make,
               '\s*[\[(][^\])]*[\])]\s*', ' ', 'g'),
               '\s*-\s*', '-', 'g'),
               '\s+', ' ', 'g'))) AS klucz
    FROM app.auction
    WHERE make IS NOT NULL
) AS k
LEFT JOIN (
    VALUES
        ('vw', 'Volkswagen'),
        ('mercedes', 'Mercedes-Benz'),
        ('mercedes benz', 'Mercedes-Benz'),
        ('mercedes-benz', 'Mercedes-Benz'),
        ('skoda', 'Škoda'),
        ('škoda', 'Škoda'),
        ('citroen', 'Citroën'),
        ('citroën', 'Citroën'),
        ('landrover', 'Land Rover'),
        ('land-rover', 'Land Rover'),
        ('alfa', 'Alfa Romeo'),
        ('alfa-romeo', 'Alfa Romeo'),
        ('ds automobiles', 'DS'),
        ('mini cooper', 'MINI')
) AS alias(klucz, kanoniczna) ON alias.klucz = k.klucz
WHERE a.id = k.id
  AND k.klucz <> '';

-- 3. Model, który jest dopiskiem w nawiasie (`[BMW]`): prawdziwy model to
--    pierwsze słowo wersji, reszta wersji zostaje wersją.
UPDATE app.auction
SET model = split_part(variant, ' ', 1),
    variant = NULLIF(regexp_replace(variant, '^\S+\s*', ''), '')
WHERE model ~ '^[\[(][^\])]*[\])]$'
  AND variant IS NOT NULL
  AND variant <> '';
