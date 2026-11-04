BEGIN IMMEDIATE;
CREATE TABLE batch_imports(
 id INTEGER PRIMARY KEY, format TEXT NOT NULL CHECK(format IN ('json','csv')),
 rows_json TEXT NOT NULL, rows_hash TEXT NOT NULL, base_hash TEXT NOT NULL,
 preview_json TEXT NOT NULL, preview_hash TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'preview' CHECK(state IN ('preview','committed','obsolete')),
 result_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, committed_at TEXT);
INSERT INTO schema_migrations VALUES(8);
COMMIT;
