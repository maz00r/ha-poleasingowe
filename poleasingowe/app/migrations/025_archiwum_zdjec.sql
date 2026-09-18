-- Trwale, ograniczone rozdzielczoscia archiwum zdjec aukcji.
-- Bajty obrazu pozostaja w /data/archiwum-zdjec; baza przechowuje tylko
-- stan kolejki i male metadane potrzebne do bezpiecznego serwowania plikow.

CREATE TABLE app.photo_archive_state (
    auction_id      bigint      PRIMARY KEY
                                REFERENCES app.auction (id) ON DELETE CASCADE,
    target          text        NOT NULL DEFAULT 'COVER',
    status          text        NOT NULL DEFAULT 'PENDING',
    source_urls     text[]      NOT NULL DEFAULT '{}',
    expected_count  integer,
    archived_count  integer     NOT NULL DEFAULT 0,
    attempts        integer     NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT photo_archive_target_check
        CHECK (target IN ('COVER', 'FULL')),
    CONSTRAINT photo_archive_status_check
        CHECK (status IN ('PENDING', 'PARTIAL', 'COMPLETE', 'UNAVAILABLE')),
    CONSTRAINT photo_archive_counts_check
        CHECK (
            archived_count >= 0
            AND (expected_count IS NULL OR expected_count >= 0)
            AND attempts >= 0
        )
);

CREATE INDEX photo_archive_queue_idx
    ON app.photo_archive_state (next_attempt_at, updated_at)
    WHERE status IN ('PENDING', 'PARTIAL');

CREATE TABLE app.auction_photo (
    auction_id  bigint      NOT NULL
                            REFERENCES app.auction (id) ON DELETE CASCADE,
    position    integer     NOT NULL,
    target      text        NOT NULL,
    width       integer     NOT NULL,
    height      integer     NOT NULL,
    byte_size   integer     NOT NULL,
    sha256      text        NOT NULL,
    archived_at timestamptz NOT NULL,

    PRIMARY KEY (auction_id, position),
    CONSTRAINT auction_photo_position_check CHECK (position >= 0),
    CONSTRAINT auction_photo_target_check CHECK (target IN ('COVER', 'FULL')),
    CONSTRAINT auction_photo_dimensions_check
        CHECK (width > 0 AND height > 0 AND byte_size > 0),
    CONSTRAINT auction_photo_sha256_check CHECK (length(sha256) = 64)
);

-- Best-effort backfill. Obserwowane aukcje zaczynaja od pelnej galerii,
-- pozostale od lekkiej okladki.
INSERT INTO app.photo_archive_state (auction_id, target, status)
SELECT
    a.id,
    CASE WHEN w.auction_id IS NULL THEN 'COVER' ELSE 'FULL' END,
    'PENDING'
FROM app.auction AS a
LEFT JOIN app.watchlist AS w ON w.auction_id = a.id
ON CONFLICT (auction_id) DO NOTHING;
