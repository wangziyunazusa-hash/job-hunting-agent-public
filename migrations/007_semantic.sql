BEGIN IMMEDIATE;
CREATE TABLE semantic_requests(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 source_link_id INTEGER NOT NULL REFERENCES job_source_links(id), jd_hash TEXT NOT NULL,
 input_json TEXT NOT NULL, input_hash TEXT NOT NULL, context_hash TEXT NOT NULL,
 prompt_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE semantic_reports(
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES semantic_requests(id),
 result_json TEXT NOT NULL, result_hash TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'review_required' CHECK(status IN ('review_required','reviewed','obsolete','rejected')),
 origin TEXT NOT NULL CHECK(origin='manual_import'), note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations VALUES(7);
COMMIT;
