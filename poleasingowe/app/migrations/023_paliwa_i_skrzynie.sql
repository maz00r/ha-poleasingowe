-- 023_paliwa_i_skrzynie.sql — zamknięte listy paliwa i skrzyni biegów.
--
-- Słownik dokładnych dopasowań (007, 011) przepuszczał każdy nowy wariant
-- jako osobną pozycję filtra: kilka rodzajów hybryd, „Automat" obok
-- „Automatyczna", wartości niebędące paliwem. Od 0.29.14 `paliwa.py`
-- i `skrzynie.py` rozpoznają po słowach kluczowych i oddają WYŁĄCZNIE:
--
--   paliwo:   Benzyna · Diesel · Hybryda · Elektryczny · Wodór · Gaz
--   skrzynia: Automatyczna · Manualna
--
-- a wszystko inne — NULL. Ta migracja robi to samo z wierszami JUŻ
-- ZEBRANYMI, także zakończonymi (te niosą ceny końcowe, §8.4).
--
-- Kolejność CASE jest tą samą kolejnością co `WZORCE` w Pythonie i ma
-- znaczenie: „hybryda/benzyna" → Hybryda, „benzyna+LPG" → Gaz.
-- `\y` to granica słowa w POSIX (`\b` w Pythonie). Ogonki zdejmuje
-- `translate`, bo rozszerzenia `unaccent` nie ma (§8.3).
--
-- UWAGA NA ROZJAZD: wzorce są tu POWTÓRZONE za kodem. Pilnują tego
-- `test_paliwa.py` i `test_skrzynie.py`, które czytają ten plik.

UPDATE app.auction
SET fuel = CASE
    WHEN k ~* 'wodor|hydrogen|fcev' THEN 'Wodór'
    WHEN k ~* 'hybr|\yphev\y|\ymhev\y|\yhev\y|plug-?in' THEN 'Hybryda'
    WHEN k ~* '\ylpg\y|\ycng\y|\ylng\y|\ygaz\y|\ygas\y|instalacj' THEN 'Gaz'
    WHEN k ~* 'elektr|electric|\yev\y|\ybev\y' THEN 'Elektryczny'
    WHEN k ~* 'diesel|olej|napedow|\yon\y|\ytdi\y|\yhdi\y|\ycrdi\y|\ydci\y' THEN 'Diesel'
    WHEN k ~* 'benz|petrol|gasoline|etylin|\ypb\y' THEN 'Benzyna'
    ELSE NULL
END
FROM (
    SELECT id,
           translate(lower(regexp_replace(fuel, '\s+', ' ', 'g')),
                     'ąćęłńóśżź', 'acelnoszz') AS k
    FROM app.auction
    WHERE fuel IS NOT NULL
) AS z
WHERE app.auction.id = z.id;

UPDATE app.auction
SET gearbox = CASE
    WHEN k ~* 'autom|\ydsg\y|\ycvt\y|\yamt\y|\yat\y|\ya/t\y|tiptronic|multitronic|steptronic|s[- ]?tronic|powershift|\yedc\y|\ydct\y|bezstopniow' THEN 'Automatyczna'
    WHEN k ~* 'manual|reczn|\ymt\y|\ym/t\y|mechaniczn' THEN 'Manualna'
    ELSE NULL
END
FROM (
    SELECT id,
           translate(lower(regexp_replace(gearbox, '\s+', ' ', 'g')),
                     'ąćęłńóśżź', 'acelnoszz') AS k
    FROM app.auction
    WHERE gearbox IS NOT NULL
) AS z
WHERE app.auction.id = z.id;
