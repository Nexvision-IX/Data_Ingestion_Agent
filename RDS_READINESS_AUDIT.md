# RDS Readiness Audit

## Scope

Repository scan for `sqlite3`, `.db`, `sqlite://`, `connect(`, `DB_PATH`, `API_BASE_URL`, `SAP_BASE_URL`, `KEFRON_BASE_URL`, `GROQ_API_KEY`, and `DATABASE_URL`. Generated dependency/build directories and `.git` internals were excluded. No application code was changed.

## Executive summary

The codebase is **not yet ready for a complete migration to an RDS PostgreSQL database**. There are two database stores:

1. The AP Agent store uses SQLAlchemy and `DATABASE_URL`. Its data-access code is largely portable, but its current environment/default selects SQLite and the dependency list has no PostgreSQL driver.
2. The AP master/ingestion store is tightly coupled to the local `data/master/ap_master.db` file through `sqlite3`, filesystem checks, SQLite parameter markers, `PRAGMA`, and `rowid`. This is the primary migration blocker.

The top-level Streamlit application also reads both SQLite files directly, so migrating only the agent service would leave the UI dependent on local `.db` files.

## Findings

| File path | Current usage | Blocks RDS migration? | Recommended change |
|---|---|---:|---|
| `.env` | Defines `SAP_BASE_URL` and `KEFRON_BASE_URL`; also contains a populated `GROQ_API_KEY` (value intentionally omitted). | No for RDS; **critical secret-management issue**. | Rotate the exposed Groq key and move secrets to the deployment secret store. Keep service URLs environment-specific; do not commit populated `.env` files. |
| `.gitignore` | Ignores `*.db`, `*.sqlite3`, `data/master/ap_master.db`, and `agent_app/*.db`. `Thumbs.db` matches the requested `.db` scan but is unrelated. | No. | Retain local DB ignores for development/export artifacts. Add/confirm ignores for populated environment files; do not treat ignore rules as a migration mechanism. |
| `agent_app/.env` | Sets `DATABASE_URL=sqlite:///./ap_agent.db`, `AP_MASTER_DB_PATH=../data/master/ap_master.db`, and `POSTED_INVOICE_API_BASE_URL`. | **Yes.** Both agent and master data are configured as local SQLite files. | Set `DATABASE_URL` to the RDS SQLAlchemy URL supplied through a secret store. Replace `AP_MASTER_DB_PATH` with a master database URL or consolidate master tables into the same RDS database. |
| `agent_app/app/config.py` | Reads `DATABASE_URL`, defaulting to SQLite; models master data as a `Path` via `AP_MASTER_DB_PATH`; reads `API_BASE_URL` and posted-invoice API URL. | **Yes** for the master path; partial blocker for agent DB because the fallback silently selects SQLite. | Require `DATABASE_URL` outside local development. Introduce `AP_MASTER_DATABASE_URL` (or one shared URL) as a string, not `Path`; validate production settings at startup. |
| `agent_app/app/db.py` | Creates a SQLAlchemy engine from `settings.database_url`; applies SQLite-only `check_same_thread` args conditionally. | Partially. The conditional is portable, but RDS cannot connect without a PostgreSQL driver and valid URL. | Keep conditional SQLite args for local dev if desired. Add an RDS-compatible driver (for example `psycopg`) and production engine options such as `pool_pre_ping`, bounded pooling, and TLS parameters in the deployed URL. |
| `agent_app/app/models.py` | Imports SQLAlchemy `Base` and defines portable ORM models using standard types, relationships, and JSON columns. The scan match is `app.db`, not a `.db` filename. | No direct blocker. | Validate generated PostgreSQL DDL and JSON behavior; manage schema with migrations rather than relying only on `create_all`. |
| `agent_app/app/main.py` | Imports `Base`/`engine` and runs `Base.metadata.create_all(bind=engine)` at import/startup. The scan match is `app.db`. | Not a connection blocker, but a production-readiness gap. | Introduce Alembic (or equivalent) migrations and run them as a deployment step; avoid using `create_all` as the production migration strategy. |
| `agent_app/app/api/routes.py` | Uses the SQLAlchemy engine/session dependency; scan match is the `app.db` import. | No direct blocker. | No RDS-specific rewrite expected once engine configuration and migrations are in place. |
| `agent_app/reset_demo.py` | Drops/recreates ORM metadata through the configured SQLAlchemy engine; scan match is the `app.db` import. | No technical blocker; operationally dangerous against shared RDS. | Guard this utility so it cannot run in staging/production, and use migrations or scoped test databases instead. |
| `agent_app/app/services/intake_service.py` | `self.db` is a SQLAlchemy `Session`; `add`, `flush`, `commit`, and `refresh` matches are not SQLite connections. | No. | No RDS-specific change expected; add transaction/concurrency tests against PostgreSQL. |
| `agent_app/app/services/orchestrator.py` | Uses a SQLAlchemy `Session`; `self.db.*` matches are ORM operations. Also consumes the posted-invoice API base URL. | No direct blocker. | Keep ORM access. Validate transaction boundaries and concurrent workflow behavior on PostgreSQL; keep API URLs environment-driven. |
| `agent_app/app/services/ap_master_trigger_service.py` | Reads `invoice_master` directly from `AP_MASTER_DB_PATH` using `sqlite3`, file-existence checks, `sqlite3.Row`, `?` parameters, and `LIMIT ?`; writes agent records through SQLAlchemy. | **Yes—primary blocker.** | Replace the SQLite connection with a SQLAlchemy repository/engine targeting RDS. Remove filesystem checks and `Path` metadata, use portable bound parameters, and define transaction/isolation behavior between source reads and agent writes. |
| `agent_app/app/integrations/sap/ap_master_gateway.py` | `APMasterSQLiteGateway` reads PO/GRN/invoice context directly from the master `.db` using `sqlite3`, file checks, `sqlite3.Row`, and `?` parameters. | **Yes—primary blocker.** | Implement an RDS-backed gateway/repository using SQLAlchemy (or a PostgreSQL driver), rename provider/source identifiers, and select it via configuration. Remove assumptions that the source is a local file. |
| `app.py` | Streamlit UI imports `sqlite3`; hardcodes master and agent `.db` paths; directly queries both files in multiple functions; checks agent DB file existence. `API_BASE_URL` is hardcoded for SAP POST calls. | **Yes—primary blocker.** | Route UI reads/writes through service APIs, or use shared SQLAlchemy repositories configured by database URLs. Remove file-existence checks and local path messages. Move `API_BASE_URL` and credentials to environment/secret configuration. |
| `ingestion/master_ingestion.py` | Creates and writes the master SQLite DB. Uses `sqlite3`, a local `MASTER_DB_PATH`, directory creation, `PRAGMA`, `?` placeholders, SQLite-style upserts, and `rowid` in retention logic. | **Yes—primary blocker.** | Refactor to an RDS-backed repository/SQLAlchemy engine. Create schema through migrations, use typed date/numeric/JSON columns, portable bound statements/PostgreSQL upserts, and replace `rowid` retention with an explicit key/order column. |
| `ingestion/master_ingestion_backup.py` | Backup copy of the same SQLite ingestion implementation, including local path, `PRAGMA`, `?` placeholders, upserts, and `rowid`. | Yes if executable or used as rollback code; otherwise it remains misleading/dead SQLite code. | Remove from runtime packaging or update/archive it alongside the primary implementation. Do not leave it as an undocumented fallback after migration. |
| `check_master_db.py` | Local diagnostic script opens `data/master/ap_master.db` with `sqlite3` and inspects schema via `PRAGMA table_info`. | Yes for RDS diagnostics, though not for application runtime. | Replace with an engine/URL-driven health/schema check using SQLAlchemy inspection or PostgreSQL catalog queries; avoid filesystem existence checks. |
| `ingestion/config.py` | Hardcodes `SAP_BASE_URL` and `KEFRON_BASE_URL` to localhost. | No direct RDS blocker. | Read both URLs and credentials from environment/secret configuration and validate them at startup. |
| `ingestion/clients/sap_client.py` | Imports and uses `SAP_BASE_URL` as its HTTP client base URL. | No. | No database change; consume environment-backed config and configure timeout/retry/TLS policy for deployment. |
| `ingestion/clients/kefron_client.py` | Imports and uses `KEFRON_BASE_URL` as its HTTP client base URL. | No. | No database change; consume environment-backed config and configure timeout/retry/TLS policy for deployment. |
| `unstructured_ingestion/vision_llm_extractor.py` | Loads `GROQ_API_KEY` from the environment and passes it to the Groq client. | No. | Keep environment-based lookup, but source the key from the deployment secret store and fail clearly when absent. Rotate the currently exposed key. |
| `unstructured_ingestion/vision_llm_extractor_backup.py` | Backup extractor also reads `GROQ_API_KEY` from the environment. | No. | Remove from runtime packaging or keep synchronized; never embed or log the key. |
| `agent_app/ap_agent.db` | Existing local SQLite agent database artifact found in the workspace. | **Yes** if it contains data that must be preserved. | Inventory and migrate required rows to RDS with a one-time, validated migration; then stop using the file in deployed environments. |
| `data/master/ap_master.db` | Existing local SQLite master database artifact found in the workspace. | **Yes** if it is the current system of record or contains migration data. | Profile row counts/types/constraints, migrate to RDS, reconcile totals and keys, then cut ingestion and all consumers over together. Retain a read-only backup per retention policy. |
| `requirements.txt` | Declares SQLAlchemy but no PostgreSQL driver (`psycopg`, `psycopg2`, `asyncpg`, or `pg8000`) was found. This is a supplemental readiness finding. | **Yes** for connecting SQLAlchemy to PostgreSQL RDS. | Add and pin the selected PostgreSQL driver; test installation and SSL connectivity in the deployment image. |

## Recommended migration sequence

1. Rotate the exposed Groq credential and move all secrets/production URLs into the deployment secret/configuration system.
2. Choose the target topology: one RDS database containing agent and master schemas, or separate database URLs with explicit ownership.
3. Add the PostgreSQL driver and schema migration tooling; create typed RDS schemas and indexes.
4. Refactor `ingestion/master_ingestion.py` to write RDS and remove SQLite-only SQL (`PRAGMA`, `?` bindings, and `rowid`).
5. Replace the two master SQLite readers with an RDS repository/gateway.
6. Replace `app.py` direct SQLite access with service/API or SQLAlchemy access.
7. Migrate and reconcile both existing `.db` files, then switch configuration atomically.
8. Run integration tests for upserts, JSON/date/numeric handling, concurrent processing, transaction rollback, connection pooling, TLS, and failure recovery before retiring SQLite.

## Readiness decision

**Current status: Blocked.** RDS cutover requires changes to the master ingestion writer, both master readers, the Streamlit UI's direct database access, production database configuration/dependencies, and a data migration plan for both SQLite files.
