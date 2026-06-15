# Production Backups

Production Postgres is currently small enough for low-cost logical backups.
On 2026-05-03, `pg_database_size('beacon')` was about 55 MB.

## Create A Backup

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup_prod_postgres.ps1
```

The script writes compressed custom-format dumps to:

```text
C:\gtm-prototype\tmp\prod-db-backups
```

It keeps the latest 14 dumps by default.

## Upload To Blob Storage

The backup script can upload directly to Azure Blob Storage when given a
container SAS URL:

```powershell
$env:PROD_BACKUP_BLOB_CONTAINER_SAS_URL = "https://<account>.blob.core.windows.net/<container>?<sas>"
powershell -ExecutionPolicy Bypass -File .\scripts\backup_prod_postgres.ps1
```

To upload an existing backup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_backup_to_blob.ps1 `
  -ContainerSasUrl "https://<account>.blob.core.windows.net/<container>?<sas>" `
  -Files @(
    "C:\gtm-prototype\tmp\prod-db-backups\beacon-prod-YYYYMMDD-HHMMSS.dump",
    "C:\gtm-prototype\tmp\prod-db-backups\beacon-prod-YYYYMMDD-HHMMSS.txt"
  )
```

Recommended storage settings:

- Container access: private.
- SAS permissions: create/write/list only for upload jobs; short expiry for
  one-off uploads.
- Lifecycle rule: keep daily backups for 14 days, weekly backups for 8 weeks,
  then delete.
- Access tier: Cool is enough for these small restore-only dumps.

## Restore Shape

The dump format is `pg_dump -Fc`, so restore with `pg_restore`.

Example restore command from the generated metadata file:

```powershell
pg_restore --clean --if-exists --no-owner --no-acl -U beacon -d beacon <dump path>
```

Restore into staging or a temporary database first when investigating accidental
deletes. Avoid restoring directly over prod unless the whole database must roll
back.

## Targeted Cleanup Backups

For risky cleanup work, also export the exact rows being touched. Those backups
are stored under:

```text
C:\gtm-prototype\tmp\prod-cleanup-backups
```

This keeps accidental-delete recovery cheap without storing large full snapshots
for every cleanup.
