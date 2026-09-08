-- 009_rodzaje.sql — rodzaj pojazdu (SPEC.md §8.2, §12).
--
-- Serwisy sprzedaja w jednej kategorii samochody osobowe, furgony, ciagniki
-- siodlowe i naczepy. Bez tego rozroznienia lista aukcji miesza je ze soba,
-- a filtr po marce nie pomaga, bo naczepa tez ma marke (Krone SD).
-- Zmierzone w fixtures 2026-09-08: na dwoch stronach listy autoprzetarg.pl
-- stalo 8 osobowych, 10 dostawczych, 3 naczepy, 2 ciezarowe i 1 motocykl.
--
-- Wartosci pochodza z `RodzajPojazdu` (app/domain/enums.py). PostgreSQL-owych
-- typow enum nie uzywamy — SPEC.md §8.2 zabrania, bo ich migracje bola.
--
-- UWAGA NA ROZJAZD: regulami ponizej POWTARZAJA slownik z
-- `app/infrastructure/sources/rodzaje.py`. Powtorzenie jest konieczne (SQL nie
-- zawola Pythona), a pilnuje go test `test_rodzaje.py`, ktory czyta ten plik
-- i porownuje oba zestawy.

ALTER TABLE app.auction
    ADD COLUMN IF NOT EXISTS vehicle_kind text NOT NULL DEFAULT 'NIEZNANY';

ALTER TABLE app.auction DROP CONSTRAINT IF EXISTS auction_vehicle_kind_check;
ALTER TABLE app.auction ADD CONSTRAINT auction_vehicle_kind_check CHECK (
    vehicle_kind = ANY (ARRAY[
        'OSOBOWY','DOSTAWCZY','CIEZAROWY','MOTOCYKL','PRZYCZEPA','AUTOBUS',
        'INNY','NIEZNANY'
    ])
);

-- Indeks czesciowy pod GLOWNY widok listy: aktywne, jednego rodzaju, po
-- terminie konca. Filtr rodzaju jest domyslnie wlaczony, wiec to zapytanie
-- wykonuje sie przy kazdym wejsciu do panelu (SPEC.md §8.3 — indeksy tylko
-- pod konkretne zapytania).
CREATE INDEX IF NOT EXISTS auction_kind_ends_active_idx
    ON app.auction (vehicle_kind, ends_at)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Backfill 1/3: autoprzetarg.pl — kategoria stoi w ADRESIE juz zapisanej
-- aukcji (`/aukcja/<slug>,<id>,<Kategoria>`), wiec dla tego zrodla nie
-- zgadujemy niczego z nazwy. To wlasnie te wiersze pokazywaly sie na liscie
-- jako naczepy wsrod samochodow.
-- ---------------------------------------------------------------------------
UPDATE app.auction AS a
SET vehicle_kind = k.rodzaj
FROM app.source AS s,
     (VALUES
         ('samochody osobowe',   'OSOBOWY'),
         ('samochody dostawcze', 'DOSTAWCZY'),
         ('samochody ciezarowe', 'CIEZAROWY'),
         ('naczepy i przyczepy', 'PRZYCZEPA'),
         ('motocykle',           'MOTOCYKL'),
         ('autobusy',            'AUTOBUS'),
         ('maszyny',             'INNY'),
         ('inne',                'INNY')
     ) AS k(klucz, rodzaj)
WHERE s.id = a.source_id
  AND s.key = 'autoprzetarg'
  AND a.vehicle_kind = 'NIEZNANY'
  AND regexp_replace(
          lower(translate(
              substring(a.url from '/aukcja/[^,]*,[^,/]+,([^,/?#]+)'),
              'ąćęłńóśżź', 'acelnoszz'
          )),
          '[-_/,.]+', ' ', 'g'
      ) = k.klucz;

-- ---------------------------------------------------------------------------
-- Backfill 2/3: EFL — adapter przemiata WYLACZNIE kategorie 16 („pojazdy
-- osobowe", RECON.md §4.1), wiec kazdy wiersz z tego zrodla jest osobowy
-- z konstrukcji. To nie jest zgadywanie, tylko przepisanie tego, co robi
-- `EflSource.przemiec_liste`.
-- ---------------------------------------------------------------------------
UPDATE app.auction AS a
SET vehicle_kind = 'OSOBOWY'
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'efl'
  AND a.vehicle_kind = 'NIEZNANY';

-- ---------------------------------------------------------------------------
-- Backfill 3/3: reszta (poleasingowe.pl) — po nazwie i nadwoziu, bo to
-- jedyne, co ten serwis o rodzaju mowi. Kolejnosc regul ma znaczenie: dluzsze
-- wyrazenia przed krotszymi, bo „CIAGNIK SIODLOWY" zawiera „CIAGNIK",
-- a decyduje pierwsze trafienie. `translate` zdejmuje ogonki — rozszerzenia
-- `unaccent` nie mamy (§8.3 zabrania rozszerzen).
WITH slownik(wzorzec, rodzaj, pierwszenstwo) AS (
    VALUES
        ('ciagnik siodlowy', 'CIEZAROWY', 1),
        ('ciagnik rolniczy', 'INNY',      2),
        ('furgon blaszak',   'DOSTAWCZY', 3),
        ('furgon',           'DOSTAWCZY', 4),
        ('blaszak',          'DOSTAWCZY', 5),
        ('plandeka',         'DOSTAWCZY', 6),
        ('brygadowka',       'DOSTAWCZY', 7),
        ('izoterma',         'DOSTAWCZY', 8),
        ('wywrotka',         'CIEZAROWY', 9),
        ('podnosnik koszowy','CIEZAROWY', 10),
        ('naczepa',          'PRZYCZEPA', 11),
        ('przyczepa',        'PRZYCZEPA', 12),
        ('autobus',          'AUTOBUS',   13),
        ('kamper',           'INNY',      14),
        ('quad',             'INNY',      15),
        ('motocykl',         'MOTOCYKL',  16),
        ('skuter',           'MOTOCYKL',  17),
        ('kombi',            'OSOBOWY',   18),
        ('sedan',            'OSOBOWY',   19),
        ('hatchback',        'OSOBOWY',   20),
        ('liftback',         'OSOBOWY',   21),
        ('coupe',            'OSOBOWY',   22),
        ('kabriolet',        'OSOBOWY',   23),
        ('cabrio',           'OSOBOWY',   24),
        ('roadster',         'OSOBOWY',   25),
        ('suv',              'OSOBOWY',   26),
        ('crossover',        'OSOBOWY',   27),
        ('minivan',          'OSOBOWY',   28),
        ('kompakt',          'OSOBOWY',   29)
),
opis AS (
    SELECT
        a.id,
        lower(translate(
            concat_ws(' ', a.make, a.model, a.variant, a.body),
            'ąćęłńóśżźĄĆĘŁŃÓŚŻŹ', 'acelnoszzACELNOSZZ'
        )) AS tekst
    FROM app.auction AS a
    WHERE a.vehicle_kind = 'NIEZNANY'
),
trafienia AS (
    SELECT DISTINCT ON (o.id) o.id, s.rodzaj
    FROM opis AS o
    JOIN slownik AS s
      ON o.tekst ~ ('(^|[^a-z0-9])' || s.wzorzec || '([^a-z0-9]|$)')
    ORDER BY o.id, s.pierwszenstwo
)
UPDATE app.auction AS a
SET vehicle_kind = t.rodzaj
FROM trafienia AS t
WHERE a.id = t.id;
