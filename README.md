# Job Hunting Agent

A local application for organizing job descriptions, comparing opportunities, preparing applications, and reviewing small resume edits. Built with Python, SQLite, and Streamlit, with Codex used extensively for code generation and revision. The application UI is currently in Chinese.

## Project status

The project combines a separately configured Codex scheduled search workflow with the management application in this repository.

| Component | Current status |
| --- | --- |
| Scheduled search in Codex | A separate task searches for jobs, checks requirements against official job descriptions, shortlists opportunities, and records previous recommendations. Its private configuration and results are not included here. |
| Local management application | Implements JD import, source tracking, duplicate review, keyword comparison, resume edit review, and application preparation. |
| Automatic connection between them | Not implemented. Search results and recommendation history are not automatically synchronized with this application. |

Running this repository does not create a scheduled search task or daily notifications. Connecting search results to the application and unifying duplicate tracking remain development priorities. This is a prototype; its real-world matching accuracy and productivity impact have not been independently established.

## Features

- **Job management:** paste job descriptions or manually import CSV/JSON batches, preserving source links and JD snapshots.
- **Duplicate review:** compare company and requisition IDs or application URLs; manually review similar jobs, merge records, and undo merges.
- **Comparison and filtering:** display preference matches separately from keywords found in confirmed resume facts. Keyword coverage is not a hiring probability or eligibility decision.
- **Small resume edits:** compare the original passage, reason, replacement text, supporting facts, and diff before accepting a suggestion. No PDF/Word editing or reformatting.
- **Application preparation:** assemble confirmed Japanese or English fact text into supporting drafts and export reviewed TXT/JSON materials. No website form filling or submission.
- **Database maintenance:** check SQLite health, back up before schema upgrades, and restore to a separate copy.

Model API calls and automatic natural-language rewriting are disabled in the application. Replacement text is entered manually; automatic suggestions currently cover trailing whitespace and a limited set of keyword/evidence prompts. The six agent prompts are design contracts, not six running model agents.

## Job source access

- **LinkedIn / OpenWork:** manual import only. No unauthorized scraping or browser automation.
- **Greenhouse / Lever / Ashby:** fixed read-only API endpoints, requiring explicit permission for each source. No companies or boards are preconfigured.
- Collection runs when the user clicks a button. The application has no persistent scheduler.
- JD comparison prefers manually imported LinkedIn and OpenWork descriptions equally, with official descriptions as a fallback. Official verification and application links prioritize official sources.

## Quick start

Use Python 3.11 or later. Run these commands from the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m streamlit run app.py --server.address 127.0.0.1
```

Open the local URL shown in the terminal, usually `http://127.0.0.1:8501`. The application has no public-server authentication setup and is intended for use on your own computer.

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1` instead of the `source` command.

The first launch creates an empty database at `data/jobs.sqlite3`. No jobs, resume facts, or company settings are preloaded. No cloud database, API key, or model API configuration is required.

## Basic workflow

The labels below match the current Chinese UI.

1. **Import jobs (`导入岗位`):** enter a JD and source link. For batch imports, inspect the preview and explicitly confirm before saving.
2. **Fact library (`事实库`):** record real experience and its evidence location. Only explicitly confirmed facts are used.
3. **Jobs and matching (`岗位与匹配`):** inspect sources, possible duplicates, and keyword comparisons. Check unknown requirements manually.
4. **Small resume edits (`简历轻量修改`):** enter a short passage and supporting facts, compare replacement text, and review it. The original resume file is never modified.
5. Export preparation materials as needed, then fill in and submit the actual application yourself.

## Architecture

```text
app.py                   Streamlit UI
job_agent/
  core.py                Jobs, sources, facts, and keyword comparison
  sources.py             Permission-checked, read-only ATS collection
  pipeline.py            User-triggered collection and matching
  bulk_import.py         Batch preview and atomic import
  reviews.py             Manual duplicate review, merge, and undo
  preferences.py         Versioned preferences and explainable ranking
  light_edits.py         Small resume suggestions, diffs, and review
  resumes.py             Fact-text assembly and approval
  applications.py        Local preparation and self-reported outcomes
  semantic.py            Offline contracts; model API calls disabled
  database.py            Migrations, backups, and restore copies
migrations/              SQLite schema versions 001–009
prompts/                 Six role-specific design prompts
tests/                   Core logic, imports, permissions, and UI tests
```

The application uses Python's standard library for its core logic, Streamlit for the UI, and pytest with Streamlit AppTest for testing. It has no separate API server, vector database, or model SDK.

## Tests

```sh
python3 -m pytest -q
```

Tests use temporary databases and simulated responses. They do not access real recruitment websites or model services. Dependency ranges are in `pyproject.toml`; a dependency lockfile has not been introduced.

## Privacy and repository contents

Actual resumes, personal facts, preferences, recommendation history, databases, backups, evidence files, and credentials belong in private local storage. The `data/` directory is excluded from Git except for `.gitkeep`. Downloaded TXT/JSON materials may contain personal information and should also be kept private. The application does not encrypt the database itself.

Personal time-saving estimates are not verified by this repository. The use of Codex for implementation is separate from what a developer can independently investigate or implement.

## Further documentation

The detailed documentation below is currently in Chinese.

- [Current implementation and scope](docs/当前项目说明.md)
- [Small resume edit workflow](docs/简历轻量修改说明.md)
- [Batch imports](docs/批量导入说明.md) and [preference-based comparison](docs/偏好排序说明.md)
- [Database maintenance and backups](docs/数据库与备份说明.md)
- [Validation records](docs/验收记录.md)
- [Original technical proposal and historical design](docs/技术方案.md)

Use this README and the current implementation document as the source of truth. Earlier phase documents include historical plans that do not describe all current capabilities.
