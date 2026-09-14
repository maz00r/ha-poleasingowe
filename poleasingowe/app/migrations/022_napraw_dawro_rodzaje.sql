-- W 0.29.12 szczegóły DAWRO ignorowały kategorię deklarowaną w opisie
-- strony, więc auta bez słowa określającego nadwozie trafiały jako NIEZNANY.
-- Wyczyszczenie odcisku wymusza jednorazowe ponowne zmapowanie szczegółów.
UPDATE app.auction AS a
SET content_hash = NULL,
    next_poll_at = now()
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'dawro'
  AND a.status = 'ACTIVE'
  AND a.vehicle_kind = 'NIEZNANY';
