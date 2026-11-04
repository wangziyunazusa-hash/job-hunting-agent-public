BEGIN IMMEDIATE;
ALTER TABLE facts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
CREATE TABLE resume_versions(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 language TEXT NOT NULL CHECK(language IN ('ja','en')),
 content TEXT NOT NULL, content_hash TEXT NOT NULL,
 context_hash TEXT NOT NULL, context_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'review_required' CHECK(status IN ('review_required','approved','obsolete')),
 approval_hash TEXT, approved_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE resume_claims(
 resume_id INTEGER NOT NULL REFERENCES resume_versions(id), position INTEGER NOT NULL,
 fact_id INTEGER NOT NULL REFERENCES facts(id), original_text TEXT NOT NULL,
 evidence TEXT NOT NULL, PRIMARY KEY(resume_id,position), UNIQUE(resume_id,fact_id));
INSERT INTO schema_migrations VALUES(4);
COMMIT;
