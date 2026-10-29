CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL,
 requisition_id TEXT, status TEXT NOT NULL DEFAULT 'imported' CHECK(status IN ('imported','matched','rejected','expired')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS job_source_links(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 source TEXT NOT NULL CHECK(source IN ('official','linkedin','openwork')),
 url TEXT NOT NULL UNIQUE, application_url TEXT, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 method TEXT NOT NULL DEFAULT 'manual' CHECK(method='manual'));
CREATE TABLE IF NOT EXISTS jd_snapshots(
 id INTEGER PRIMARY KEY, source_link_id INTEGER NOT NULL REFERENCES job_source_links(id),
 content TEXT NOT NULL, content_hash TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_link_id,content_hash));
CREATE TABLE IF NOT EXISTS facts(
 id INTEGER PRIMARY KEY, category TEXT NOT NULL, text_ja TEXT NOT NULL DEFAULT '', text_en TEXT NOT NULL DEFAULT '',
 evidence TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(length(trim(text_ja))>0 OR length(trim(text_en))>0), CHECK(length(trim(evidence))>0));
CREATE TABLE IF NOT EXISTS match_reports(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 report_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_events(
 id INTEGER PRIMARY KEY, action TEXT NOT NULL, entity_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
