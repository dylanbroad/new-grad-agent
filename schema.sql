-- Job Search Copilot schema
-- applications: owned by the Application WORKFLOW (fixed control flow)
-- weak_spots:   owned by the Interview Prep AGENT (model decides what to read/write)
-- tool_call_log: shared observability across both

CREATE TABLE IF NOT EXISTS applications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company            TEXT NOT NULL,
    role               TEXT NOT NULL,
    url                TEXT NOT NULL,
    url_hash           TEXT NOT NULL UNIQUE,   -- makes intake idempotent (re-adding = update, not duplicate)
    status             TEXT NOT NULL DEFAULT 'found',  -- found -> applied -> interviewing -> offer/rejected
    jd_text            TEXT,
    resume_diff        TEXT,
    similarity_score   INTEGER,
    tailored_resume    TEXT,
    resume_pdf_path    TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weak_spots (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company            TEXT,                   -- nullable: some topics are general, not company-specific
    topic              TEXT NOT NULL,
    last_result        TEXT,                   -- 'correct' | 'partial' | 'incorrect'
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_review_date   TEXT,                   -- agent decides when to re-surface this
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company, topic)
);

CREATE TABLE IF NOT EXISTS tool_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name       TEXT NOT NULL,
    args_json       TEXT,
    success         INTEGER NOT NULL,          -- 1 / 0
    latency_ms      INTEGER,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
