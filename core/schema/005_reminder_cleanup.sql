CREATE TABLE reminder_cleanup (
 reminder_id TEXT PRIMARY KEY,
 list_name TEXT NOT NULL,
 case_id TEXT NOT NULL REFERENCES cases(case_id),
 kind TEXT NOT NULL CHECK(kind IN ('出発','到着','時刻')),
 title TEXT NOT NULL,
 created_at TEXT NOT NULL,
 completed_at TEXT,
 deleted_at TEXT,
 mismatch_at TEXT
);
CREATE INDEX idx_reminder_cleanup_case ON reminder_cleanup(case_id);
