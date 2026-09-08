-- 004_przemiat.sql — znacznik ostatniego przemiatu listy (SPEC.md §11.2).
--
-- `source.sweep_interval_seconds` mowil, JAK CZESTO przemiatac liste, ale nic
-- nie zapisywalo, KIEDY zrobiono to ostatnio. Bez tego dispatcher albo
-- przemiata przy kazdym obrocie (czyli co minute, zamiast co szesc godzin),
-- albo trzyma znacznik w pamieci procesu i przemiata po kazdym restarcie.
-- Oba warianty lamia §11.2: "zbiorczy przemiat listy raz na kilka godzin".
--
-- NULL znaczy "nigdy nie przemiatano" — pierwszy obrot dispatchera zrobi to
-- od razu, i tak ma byc: swiezo zainstalowany dodatek ma sie zapelnic, a nie
-- czekac szesc godzin z pusta lista.

ALTER TABLE app.source
    ADD COLUMN IF NOT EXISTS last_sweep_at timestamptz;

COMMENT ON COLUMN app.source.last_sweep_at IS
    'Kiedy ostatnio przemieciono liste tego zrodla (SPEC.md §11.2). '
    'NULL = nigdy; wtedy przemiat idzie przy najblizszym obrocie.';
