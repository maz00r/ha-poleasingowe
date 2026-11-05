-- 032 — ogłoszenia serwisów w tytułach aukcji (0.39.0).
--
-- Serwis dokleił do tytułu komunikat o przerwie technicznej, a tytuł rozbijamy
-- na markę, model i wersję, więc tekst trafił do `variant`. Mappery już go
-- odcinają (`marki._KOMUNIKAT_SERWISU`, ten sam wzorzec), ale zapisane wiersze
-- mogłyby nie dostać świeżego odczytu, np. po zakończeniu aukcji.

UPDATE app.auction
SET variant = NULLIF(btrim(regexp_replace(
        variant,
        '\s*\y(?:w\s+(?:poniedziałek|wtorek|środę|czwartek|piątek|sobotę|niedzielę)\s+od\s+godz|na\s+stronie\s+będą|(?:prac|przerw)\w*\s+(?:modernizacyjn|serwisow|techniczn)|serwis\s+będzie\s+niedostępny).*$',
        '',
        'i'
    )), '')
WHERE variant ~* '\y(?:w\s+(?:poniedziałek|wtorek|środę|czwartek|piątek|sobotę|niedzielę)\s+od\s+godz|na\s+stronie\s+będą|(?:prac|przerw)\w*\s+(?:modernizacyjn|serwisow|techniczn)|serwis\s+będzie\s+niedostępny)';
