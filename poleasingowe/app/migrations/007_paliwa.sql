-- 007_paliwa.sql — ujednolicenie nazw paliwa (SPEC.md §6.2, §9).
--
-- Ten sam problem co z markami (migracja 006), tylko ostrzejszy: serwisy
-- uzywaja ROZNYCH SLOW, nie tylko roznej wielkosci liter. Zmierzone
-- w danych:
--
--   poleasingowe.pl : Benzyna, Hybryda/benzyna, Olej napedowy (BEZ ogonka)
--   autoprzetarg.pl : Benzyna, Diesel, Elektryczny
--   aukcje.efl.com.pl: Hybryda, Olej napedowy (Z ogonkiem)
--
-- Czyli jedno paliwo pod trzema nazwami i hybryda pod dwiema. Lista
-- rozwijana miala piec pozycji na trzy paliwa, a filtr po ktorejkolwiek
-- gubil wyniki z pozostalych serwisow.
--
-- Klucze w obu wariantach zapisu, z ogonkami i bez — `lower()` ich nie
-- zrowna, a rozszerzenia `unaccent` nie mamy (§8.3).
--
-- UWAGA NA ROZJAZD: slownik jest tu POWTORZONY za `sources/paliwa.py`.
-- Pilnuje tego test `test_paliwa.py`, ktory czyta ten plik i porownuje
-- oba zestawy.

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
        ('hybryda/benzyna', 'Hybryda'),
        ('hybryda/olej napedowy', 'Hybryda'),
        ('hybryda/olej napędowy', 'Hybryda'),
        ('hybrid', 'Hybryda'),
        ('hybryda plug-in', 'Hybryda plug-in'),
        ('plug-in', 'Hybryda plug-in'),
        ('phev', 'Hybryda plug-in'),
        ('elektryczny', 'Elektryczny'),
        ('elektryczne', 'Elektryczny'),
        ('electric', 'Elektryczny'),
        ('ev', 'Elektryczny'),
        ('lpg', 'LPG'),
        ('benzyna+lpg', 'LPG'),
        ('benzyna/lpg', 'LPG'),
        ('cng', 'CNG')
) AS slownik(klucz, kanoniczne)
WHERE a.fuel IS NOT NULL
  AND lower(a.fuel) = slownik.klucz
  AND a.fuel <> slownik.kanoniczne;
