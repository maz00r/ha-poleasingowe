-- 014_wywolawcza.sql — cena wywolawcza z aukcji bez ofert (SPEC.md §8.2).
--
-- Zaden z czterech serwisow nie podaje ceny wywolawczej wprost (sprawdzone
-- w fixtures poleasingowe.pl: nie ma jej ani w bloku aukcji, ani w tresci
-- strony). Da sie ja jednak wywnioskowac PEWNIE: aukcja bez ani jednej
-- oferty stoi na cenie wywolawczej, bo licytowac mozna wylacznie w gore.
--
-- Od tej migracji robi to kod przy zapisie. Tu uzupelniamy wiersze JUZ
-- ZEBRANE, ktore wciaz maja `bid_count = 0` — dla nich wnioskowanie jest
-- tak samo pewne dzis, jak bylo w chwili odczytu.
--
-- Aukcji z ofertami NIE RUSZAMY: tam cena biezaca jest wyzsza od
-- wywolawczej o nieznana nam wartosc i kazda wpisana liczba bylaby
-- zmyslona. Zostaje NULL, czyli "nie wiadomo" — i to jest poprawna
-- odpowiedz (§8.2).

UPDATE app.auction
SET price_start = price_current
WHERE price_start IS NULL
  AND bid_count = 0
  AND price_current IS NOT NULL;
