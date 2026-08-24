-- PRCritiq review store.
--
-- Postgres is the source of truth for review runs (design, State Plane). There
-- is no Redis in v1: a queue nobody is contending for is infrastructure noise
-- (ADR-009).

CREATE TABLE IF NOT EXISTS installations (
    id              BIGSERIAL PRIMARY KEY,
    github_id       BIGINT      NOT NULL UNIQUE,
    account_login   TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repositories (
    id               BIGSERIAL PRIMARY KEY,
    github_id        BIGINT UNIQUE,
    full_name        TEXT        NOT NULL UNIQUE,
    default_branch   TEXT        NOT NULL DEFAULT 'main',
    visibility       TEXT        NOT NULL DEFAULT 'public',
    last_indexed_sha TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id            BIGSERIAL PRIMARY KEY,
    repository_id BIGINT      NOT NULL REFERENCES repositories (id) ON DELETE CASCADE,
    number        INTEGER     NOT NULL,
    base_sha      TEXT        NOT NULL DEFAULT '',
    head_sha      TEXT        NOT NULL DEFAULT '',
    title         TEXT        NOT NULL DEFAULT '',
    author_login  TEXT        NOT NULL DEFAULT '',
    state         TEXT        NOT NULL DEFAULT 'open',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repository_id, number)
);

-- The idempotency key is UNIQUE rather than merely indexed, so a redelivered
-- webhook cannot create a second run even if two workers race: the database
-- refuses it rather than the application remembering to check.
CREATE TABLE IF NOT EXISTS review_runs (
    id              BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT        NOT NULL UNIQUE,
    pull_request_id BIGINT      REFERENCES pull_requests (id) ON DELETE CASCADE,
    repo_full_name  TEXT        NOT NULL,
    pr_number       INTEGER     NOT NULL,
    head_sha        TEXT        NOT NULL DEFAULT '',
    mode            TEXT        NOT NULL DEFAULT 'dry-run',
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'summarized', 'posted', 'failed', 'skipped')),
    model_provider  TEXT,
    model_name      TEXT,
    routing_reason  TEXT,
    trace_provider  TEXT,
    trace_id        TEXT,
    summary         TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS review_runs_repo_pr_idx
    ON review_runs (repo_full_name, pr_number, created_at DESC);

CREATE TABLE IF NOT EXISTS review_files (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             BIGINT      NOT NULL REFERENCES review_runs (id) ON DELETE CASCADE,
    path               TEXT        NOT NULL,
    language           TEXT        NOT NULL DEFAULT 'unknown',
    status             TEXT        NOT NULL DEFAULT '',
    guardrail_decision TEXT        NOT NULL,
    guardrail_reason   TEXT        NOT NULL DEFAULT '',
    changed_lines      INTEGER     NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, path)
);

CREATE TABLE IF NOT EXISTS tool_results (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT      NOT NULL REFERENCES review_runs (id) ON DELETE CASCADE,
    tool             TEXT        NOT NULL,
    status           TEXT        NOT NULL,
    reason           TEXT        NOT NULL DEFAULT '',
    exit_code        INTEGER,
    duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    diagnostics      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, tool)
);

CREATE TABLE IF NOT EXISTS review_findings (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             BIGINT      NOT NULL REFERENCES review_runs (id) ON DELETE CASCADE,
    file_path          TEXT        NOT NULL,
    line               INTEGER     NOT NULL,
    severity           TEXT        NOT NULL,
    confidence         INTEGER     NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    category           TEXT        NOT NULL,
    finding            TEXT        NOT NULL,
    evidence           TEXT        NOT NULL DEFAULT '',
    suggested_fix      TEXT        NOT NULL DEFAULT '',
    source_refs        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    publish_decision   TEXT        NOT NULL CHECK (publish_decision IN ('publish', 'suppress')),
    suppression_reason TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A suppressed finding must carry its reason and a published one must not,
    -- so an invalid-line rate can never be computed from incomplete rows.
    CHECK (
        (publish_decision = 'suppress' AND suppression_reason IS NOT NULL)
        OR (publish_decision = 'publish' AND suppression_reason IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS review_findings_run_idx ON review_findings (run_id);

CREATE TABLE IF NOT EXISTS posted_comments (
    id                BIGSERIAL PRIMARY KEY,
    finding_id        BIGINT      NOT NULL REFERENCES review_findings (id) ON DELETE CASCADE,
    run_id            BIGINT      NOT NULL REFERENCES review_runs (id) ON DELETE CASCADE,
    github_comment_id BIGINT,
    body_hash         TEXT        NOT NULL,
    posted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Prevents reposting the same comment body on a re-run of the same review.
    UNIQUE (run_id, body_hash)
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id              BIGSERIAL PRIMARY KEY,
    dataset_version TEXT        NOT NULL,
    model_policy    TEXT        NOT NULL DEFAULT 'auto',
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    report_paths    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark_cases (
    id               BIGSERIAL PRIMARY KEY,
    benchmark_run_id BIGINT      NOT NULL REFERENCES benchmark_runs (id) ON DELETE CASCADE,
    repo_full_name   TEXT        NOT NULL,
    pr_number        INTEGER     NOT NULL,
    expected_labels  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    results          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
