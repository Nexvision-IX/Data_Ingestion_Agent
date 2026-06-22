# AWS deployment environment

The AWS target uses Ubuntu EC2, PostgreSQL RDS, PM2, and the S3 bucket
`nvx-agentic-ap-invoice-data`. DNS is optional for the first deployment because
all service-to-service calls use EC2 loopback URLs.

## Services and directories

| PM2 service | EC2 directory | Entrypoint | Port |
|---|---|---|---:|
| `frontend_api` | `/var/www/frontend_api` | `app.py` (Streamlit) | 3000 |
| `backend_sap_api` | `/var/www/backend_sap_api` | `mock_api/main_api.py` | 3003 |
| `backend_agent_api` | `/var/www/backend_agent_api` | `agent_app/app/main.py` | 3006 |

Each directory must contain the complete repository release (or be a symlink to
the same release), because the services share `ap_database`, `ingestion`, and
other root packages. Avoid deploying different commits to the three paths.

The PM2 ecosystem defaults to these `/var/www` directories. For a different
layout, set `FRONTEND_APP_DIR`, `SAP_API_APP_DIR`, and `AGENT_API_APP_DIR`
before starting PM2.

## Environment setup

Copy `.env.aws.example` to `.env` in each deployed repository path and replace
all `REPLACE_*` placeholders. Do not commit populated `.env` files.

Use PostgreSQL URLs with `sslmode=require`. In `APP_ENV=aws`, both
`DATABASE_URL` and `MASTER_DATABASE_URL` are mandatory and SQLite is rejected.

Initial internal service URLs are:

```env
MOCK_API_BASE_URL=http://127.0.0.1:3003
AP_AGENT_BASE_URL=http://127.0.0.1:3006
SAP_BASE_URL=http://127.0.0.1:3003
KEFRON_BASE_URL=http://127.0.0.1:3003
POSTED_INVOICE_API_BASE_URL=http://127.0.0.1:3003
```

The future public endpoints are:

- `agentic-ap.nexvisionix.com:3000`
- `sap-api.nexvisionix.com:3003`
- `ap-agent-api.nexvisionix.com:3006`

Do not switch internal URLs to the public domains until DNS and routing are
confirmed.

## S3 configuration

Configure:

```env
STORAGE_BACKEND=s3
S3_BUCKET_NAME=nvx-agentic-ap-invoice-data
S3_REGION=REPLACE_WITH_BUCKET_REGION
S3_PREFIX=ap-demo/
S3_ENDPOINT_URL=
```

Step 15A adds the shared local/S3 storage foundation and deterministic artifact
key generation. The current upload and OCR workflows do not call it yet; that
integration belongs to Step 15B. EC2 should receive S3 permissions through an
IAM instance role, so static AWS access keys are not required.

`S3_PREFIX` is the root prefix only, such as `ap-demo/`. Invoice artifacts must
not be stored together in one flat S3 folder. Step 14B will use this hierarchy:

```text
S3_PREFIX/invoices/{invoice_number_or_upload_id}/original/{original_filename}
S3_PREFIX/invoices/{invoice_number_or_upload_id}/extracted_text/extracted.txt
S3_PREFIX/invoices/{invoice_number_or_upload_id}/extracted_json/invoice.json
S3_PREFIX/invoices/{invoice_number_or_upload_id}/metadata/processing_metadata.json
```

Example:

```text
ap-demo/invoices/INV_RDS_TEST_001/original/INV_RDS_TEST_001.pdf
ap-demo/invoices/INV_RDS_TEST_001/extracted_text/extracted.txt
ap-demo/invoices/INV_RDS_TEST_001/extracted_json/invoice.json
ap-demo/invoices/INV_RDS_TEST_001/metadata/processing_metadata.json
```

Use `invoice_number` as the artifact directory when it is available. If the
invoice number is not known at upload time, generate an `upload_id`, store the
artifacts under that identifier, and later link the `upload_id` to the extracted
invoice number in `processing_metadata.json` and RDS metadata.

RDS will later store the S3 keys or URIs for the original file, extracted OCR
text, and extracted structured JSON. RDS must store references and processing
metadata, not the file blobs themselves. Actual upload/OCR integration and RDS
artifact-key persistence belong to Step 15B.

## Python environment

Create a Python 3.11 virtual environment in each service directory (or once in
the shared release when using symlinks):

```bash
cd /var/www/frontend_api
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Repeat for independent directories. Ensure startup scripts are executable:

```bash
chmod +x scripts/start_frontend.sh scripts/start_sap_api.sh scripts/start_agent_api.sh
```

## Validate and initialize RDS

From a configured repository root:

```bash
python scripts/verify_aws_env.py
python scripts/test_rds_connection.py
python scripts/init_rds_schema.py
python scripts/check_rds_schema.py
```

Schema initialization creates missing schemas and tables but does not drop
tables or delete data. Keep these values false:

```env
AUTO_CREATE_AGENT_TABLES=false
ALLOW_DESTRUCTIVE_AGENT_RESET=false
ALLOW_DESTRUCTIVE_MASTER_RESET=false
```

## Start with PM2

Run the ecosystem file from the repository containing it:

```bash
pm2 start ecosystem.config.js
pm2 logs
pm2 save
```

Useful checks:

```bash
pm2 status
curl http://127.0.0.1:3003/health
curl http://127.0.0.1:3006/health
curl -I http://127.0.0.1:3000
```

After the first successful deployment, configure `pm2 startup` using the exact
command PM2 prints for the Ubuntu user.
