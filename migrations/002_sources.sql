PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;
CREATE TABLE job_source_links_new(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 source TEXT NOT NULL CHECK(source IN ('official','linkedin','openwork')),
 url TEXT NOT NULL UNIQUE, application_url TEXT, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 method TEXT NOT NULL DEFAULT 'manual' CHECK(method IN ('manual','api')), latest_snapshot_id INTEGER REFERENCES jd_snapshots(id), last_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO job_source_links_new SELECT l.*, (SELECT MAX(id) FROM jd_snapshots WHERE source_link_id=l.id), l.imported_at FROM job_source_links l;
DROP TABLE job_source_links;
ALTER TABLE job_source_links_new RENAME TO job_source_links;
CREATE TABLE source_permissions(
 id INTEGER PRIMARY KEY, provider TEXT NOT NULL CHECK(provider IN ('greenhouse','lever','lever_eu','ashby')),
 board TEXT NOT NULL, company TEXT NOT NULL, evidence TEXT NOT NULL, official_url TEXT NOT NULL,
 expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sync_runs(
 id INTEGER PRIMARY KEY, permission_id INTEGER NOT NULL REFERENCES source_permissions(id),
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
 error TEXT, item_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE source_postings(
 permission_id INTEGER NOT NULL REFERENCES source_permissions(id), external_id TEXT NOT NULL,
 source_link_id INTEGER NOT NULL REFERENCES job_source_links(id), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 last_seen_run INTEGER NOT NULL REFERENCES sync_runs(id), PRIMARY KEY(permission_id,external_id));
CREATE TABLE api_cache(
 permission_id INTEGER NOT NULL REFERENCES source_permissions(id), url TEXT NOT NULL,
 payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(permission_id,url));
INSERT INTO schema_migrations VALUES(2);
COMMIT;
PRAGMA foreign_keys=ON;
