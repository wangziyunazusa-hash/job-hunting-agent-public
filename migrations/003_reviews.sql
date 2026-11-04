BEGIN IMMEDIATE;
ALTER TABLE jobs ADD COLUMN merged_into INTEGER REFERENCES jobs(id);
CREATE TABLE job_merges(
 id INTEGER PRIMARY KEY, source_job_id INTEGER NOT NULL REFERENCES jobs(id),
 target_job_id INTEGER NOT NULL REFERENCES jobs(id), link_ids_json TEXT NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, undone_at TEXT);
CREATE TABLE duplicate_reviews(
 left_job_id INTEGER NOT NULL REFERENCES jobs(id), right_job_id INTEGER NOT NULL REFERENCES jobs(id),
 reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(left_job_id,right_job_id), CHECK(left_job_id<right_job_id));
INSERT INTO schema_migrations VALUES(3);
COMMIT;
