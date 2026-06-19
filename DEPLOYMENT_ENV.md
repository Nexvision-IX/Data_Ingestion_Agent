# Environment configuration

Use `.env.example` for the root Streamlit/ingestion process and
`agent_app/.env.example` for the Agent API process. Copy them to `.env` only for
local development; populated `.env` files must remain uncommitted.

For EC2/RDS, inject `DATABASE_URL`, `MASTER_DATABASE_URL`, API credentials, and
SMTP/LLM secrets at runtime using AWS Secrets Manager or SSM Parameter Store.
Use `APP_ENV=aws`, PostgreSQL URLs with `sslmode=require`, and leave all
auto-create/destructive reset flags false unless a one-time operation is
explicitly intended.

`AP_MASTER_DB_PATH` is legacy and no longer used by the Agent API. Configure
master access with `MASTER_DATABASE_URL`.

Before starting services, initialize and validate the configured databases:

```powershell
python scripts/init_rds_schema.py
python scripts/check_rds_schema.py
```

Initialization is idempotent and non-destructive: it creates missing schemas
and tables but never drops tables or deletes data.
