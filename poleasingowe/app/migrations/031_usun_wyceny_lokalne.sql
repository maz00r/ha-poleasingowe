-- 031 — koniec wycen bez ogłoszeń z Otomoto (0.38.0).
--
-- Taka wycena opierała się na pamięci modelu, a jej „ceny z portali" były
-- szacunkiem, nie odczytem. Aplikacja już ich nie liczy ani nie umie pokazać.
-- Automat zastępował je tylko na aktywnych aukcjach, więc na zakończonych
-- zostałyby na stałe. Kolumna `source_kind` zostaje: filtrują po niej kolejka
-- automatu i ranking okazji.

DELETE FROM app.ai_valuation WHERE source_kind = 'LOKALNA';
