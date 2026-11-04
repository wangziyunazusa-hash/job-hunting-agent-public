BEGIN IMMEDIATE;
CREATE TABLE search_profiles(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, preferences_json TEXT NOT NULL,
 preferences_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE ranking_runs(
 id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES search_profiles(id),
 context_hash TEXT NOT NULL, report_json TEXT NOT NULL, report_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations VALUES(6);
COMMIT;
