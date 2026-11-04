BEGIN IMMEDIATE;
CREATE TABLE applications(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 resume_id INTEGER NOT NULL REFERENCES resume_versions(id),
 state TEXT NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared','awaiting_user_submit','needs_review','user_reported_submitted','abandoned')),
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 readiness_hash TEXT, user_reported_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE application_events(
 id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id),
 event TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('user','system')),
 note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations VALUES(5);
COMMIT;
