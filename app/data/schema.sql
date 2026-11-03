CREATE TABLE IF NOT EXISTS tickets (
    id             TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'processing', 'classified', 'failed')),
    category       TEXT CHECK (category IN ('billing', 'technical', 'account', 'other')),
    priority       TEXT CHECK (priority IN ('low', 'medium', 'high')),
    summary        TEXT,
    model          TEXT,
    prompt_version TEXT,
    last_error     TEXT,
    attempts       INT NOT NULL DEFAULT 0,
    locked_until   TIMESTAMPTZ,
    run_after      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status <> 'classified' OR (category IS NOT NULL AND priority IS NOT NULL AND summary IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS tickets_queue_idx ON tickets (run_after)
    WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS tickets_list_idx ON tickets (category, priority, created_at DESC, id DESC);
