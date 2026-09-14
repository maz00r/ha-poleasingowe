-- Po odrzuceniu nieskategoryzowanej ścieżki awaryjnej potrzebujemy od razu
-- odczytać prawidłowe listy osobowych i dostawczych, a nie czekać do końca
-- starego interwału przemiatania.
UPDATE app.source
SET last_sweep_at = NULL,
    last_sweep_attempt_at = NULL,
    last_sweep_status = 'UNKNOWN'
WHERE key = 'mleasing';
