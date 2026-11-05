BEGIN IMMEDIATE;
CREATE TABLE resume_passages(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), language TEXT NOT NULL CHECK(language IN ('ja','en')),
 section TEXT NOT NULL, original_text TEXT NOT NULL, locked_json TEXT NOT NULL,
 facts_json TEXT NOT NULL, input_hash TEXT NOT NULL, context_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE resume_edit_suggestions(
 id INTEGER PRIMARY KEY, passage_id INTEGER NOT NULL REFERENCES resume_passages(id),
 replacement TEXT NOT NULL, reason TEXT NOT NULL, jd_quote TEXT NOT NULL,
 suggestion_hash TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('manual','whitespace_rule')),
 status TEXT NOT NULL DEFAULT 'review_required' CHECK(status IN ('review_required','accepted','rejected','obsolete')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations VALUES(9);
COMMIT;
