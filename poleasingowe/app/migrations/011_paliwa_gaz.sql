-- 011_paliwa_gaz.sql — „benzyna + gaz" to to samo co „benzyna + LPG" (§6.2).
--
-- Migracja 007 zrownala nazwy paliw, ale porownywala je DOSLOWNIE. Serwisy
-- sklejaja paliwo z instalacja roznymi znakami: `Benzyna+LPG`, `Benzyna / LPG`,
-- `Benzyna i gaz`, `Benzyna z instalacja gazowa`, a czasem pisza samo `LPG`
-- albo samo `Gaz`. To jest jedno i to samo auto — instalacja gazowa jest
-- zawsze DODATKIEM do benzyny — a w filtrze robilo sie z tego kilka osobnych
-- pozycji, z ktorych kazda gubila czesc ofert.
--
-- Od tej wersji `paliwa.kanoniczne_paliwo` sprowadza separator do spacji,
-- zanim zajrzy do slownika. Ta migracja robi to samo z wierszami JUZ
-- ZEBRANYMI — w tym z aukcjami zakonczonymi, ktorych przemiat juz nie dotknie,
-- a to one nios/a ceny koncowe (§8.4).
--
-- UWAGA NA ROZJAZD: slownik jest tu POWTORZONY za kodem Pythona. Pilnuje tego
-- `test_paliwa.py`, ktory czyta ten plik i porownuje oba zestawy.

UPDATE app.auction AS a
SET fuel = slownik.kanoniczne
FROM (
    VALUES
        ('olej napedowy', 'Diesel'),
        ('olej napędowy', 'Diesel'),
        ('on', 'Diesel'),
        ('diesel', 'Diesel'),
        ('benzyna', 'Benzyna'),
        ('petrol', 'Benzyna'),
        ('pb', 'Benzyna'),
        ('hybryda', 'Hybryda'),
        ('hybryda benzyna', 'Hybryda'),
        ('hybryda olej napedowy', 'Hybryda'),
        ('hybryda olej napędowy', 'Hybryda'),
        ('hybrid', 'Hybryda'),
        ('hybryda plug-in', 'Hybryda plug-in'),
        ('plug-in', 'Hybryda plug-in'),
        ('phev', 'Hybryda plug-in'),
        ('elektryczny', 'Elektryczny'),
        ('elektryczne', 'Elektryczny'),
        ('electric', 'Elektryczny'),
        ('ev', 'Elektryczny'),
        ('lpg', 'LPG'),
        ('gaz', 'LPG'),
        ('benzyna lpg', 'LPG'),
        ('benzyna gaz', 'LPG'),
        ('benzyna z instalacja gazowa', 'LPG'),
        ('benzyna instalacja gazowa', 'LPG'),
        ('lpg benzyna', 'LPG'),
        ('cng', 'CNG'),
        ('benzyna cng', 'CNG')
) AS slownik(klucz, kanoniczne)
WHERE a.fuel IS NOT NULL
  -- Ta sama normalizacja co w `paliwa._SEPARATORY`: `+`, `/`, `,`, `&`
  -- oraz samodzielne „i" i „z" staja sie pojedyncza spacja.
  AND btrim(
        regexp_replace(
            lower(a.fuel), '\s*([+/,&]|\yi\y|\yz\y)\s*', ' ', 'g'
        )
      ) = slownik.klucz
  AND a.fuel <> slownik.kanoniczne;
