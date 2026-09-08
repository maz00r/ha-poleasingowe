-- 005_uzupelnij_terminy.sql — jednorazowy odpyt dla aukcji bez terminu.
--
-- Aukcje odkryte przez przemiat listy PRZED wersja 0.5.0 dostawaly
-- `next_poll_at = NULL`, bo obowiazywala wtedy dosłowna lektura §11.2:
-- "nieobserwowane nie sa odpytywane pojedynczo w ogole". Skutek uboczny byl
-- taki, ze nigdy nie dostawaly odpytu szczegolow — a lista poleasingowe.pl
-- podaje sama date dzienna, bez godziny (RECON.md §4.2). Te aukcje zostawaly
-- wiec z pustym `ends_at` NA ZAWSZE: przemiat go nie uzupelnia (COALESCE
-- z NULL-a to nadal NULL), a odpytu nie bylo.
--
-- W interfejsie wygladalo to tak, ze kolumna "Koniec" byla pusta dla pozycji,
-- ktore na stronie serwisu maja date zakonczenia podana wprost.
--
-- Ta migracja planuje im ten jeden odpyt. Dispatcher wykona go przy
-- najblizszym obrocie i — zgodnie z reguła z §11.2 — wyzeruje `next_poll_at`
-- z powrotem, o ile aukcja nie jest obserwowana.

UPDATE app.auction
SET next_poll_at = now(),
    poll_tier = 'FAR'
WHERE status = 'ACTIVE'
  AND ends_at IS NULL
  AND next_poll_at IS NULL;
