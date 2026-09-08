-- 006_marki.sql — ujednolicenie zapisu marek (SPEC.md §6.2, §9).
--
-- Serwisy pisza marke roznie: poleasingowe.pl i autoprzetarg.pl WERSALIKAMI
-- (`TESLA`, `VOLKSWAGEN`), EFL zwyklym zapisem (`Audi`). Skutki widac wprost
-- w interfejsie i w raportach:
--
-- * lista rozwijana marek ma po dwa wpisy na marke (`TESLA` i `Tesla`),
-- * filtr po jednej z nich gubi polowe wynikow, bo porownanie jest scisle,
-- * `reporting.v_market_stats` liczy DWIE osobne mediany dla tego samego
--   modelu, czyli daje falszywy obraz rynku.
--
-- Od tej wersji mapper zapisuje postac kanoniczna (`marki.kanoniczna_marka`).
-- Ta migracja doprowadza do niej wiersze JUZ ZEBRANE — w tym aukcje
-- zakonczone, ktorych przemiat listy juz nie dotknie, a to wlasnie one niosa
-- ceny koncowe (§8.4).
--
-- UWAGA NA ROZJAZD: listy skrotow i aliasow sa tu POWTORZONE za kodem
-- Pythona. Pilnuje tego test `test_marki.py`, ktory czyta ten plik
-- i porownuje oba zestawy — bez niego rozjechalyby sie przy pierwszej
-- dopisanej marce.

-- 1. Postac podstawowa: `MERCEDES-BENZ` -> `Mercedes-Benz`, `TESLA` -> `Tesla`.
--    `initcap` zaczyna slowo po kazdym znaku niealfanumerycznym, wiec myslnik
--    obsluguje sam.
UPDATE app.auction
SET make = initcap(make)
WHERE make IS NOT NULL AND make <> initcap(make);

-- 2. Skroty zostaja wersalikami — `Bmw` i `Man` wygladaja na literowke.
UPDATE app.auction
SET make = upper(make)
WHERE make IS NOT NULL
  AND upper(make) = ANY (
      ARRAY['BMW','MAN','DAF','MG','DS','SEAT','KTM','JCB','BYD','FAW','GMC','RAM']
  );

-- 3. Aliasy: rozne nazwy tej samej marki. Klucze w obu wariantach zapisu,
--    bo bez rozszerzenia `unaccent` (§8.3 zabrania rozszerzen) `lower()` nie
--    zrowna `Skoda` ze `Škoda`.
UPDATE app.auction AS a
SET make = alias.kanoniczna
FROM (
    VALUES
        ('vw', 'Volkswagen'),
        ('mercedes', 'Mercedes-Benz'),
        ('skoda', 'Škoda'),
        ('škoda', 'Škoda'),
        ('citroen', 'Citroën'),
        ('citroën', 'Citroën')
) AS alias(klucz, kanoniczna)
WHERE a.make IS NOT NULL
  AND lower(a.make) = alias.klucz
  AND a.make <> alias.kanoniczna;
