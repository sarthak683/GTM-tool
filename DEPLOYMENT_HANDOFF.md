# Deployment Handoff

This file is the single source of truth for how Beacon GTM is deployed today, where the important files live, and what another AI or engineer needs in order to inspect, build, deploy, and debug staging or production safely.

## Repo

- Repo root: `C:\gtm-prototype`
- Active branch used during current deployment work: `experiment/celery-account-sourcing-workers`

## Current Environments

### Staging

- Namespace: `gtm`
- Public URL: `https://gtm.staging2.beacon.li/`
- Helm release: `gtm`
- Latest known staging Helm revision: `69`
- Backend image: `beacon.azurecr.io/gtm-be:v0.12-7965ed2`
- Frontend image: `beacon.azurecr.io/gtm-fe:v0.12-7965ed2`

### Production

- Namespace: `gtm-prod`
- Public URL: `https://gtm.beacon.li/`
- Helm release: `gtm`
- Latest known production Helm revision: `41`
- Backend image: `beacon.azurecr.io/gtm-be:v0.13-49cee30`
- Frontend image: `beacon.azurecr.io/gtm-fe:v0.14-490dae8`

## Cluster Access

- Kubeconfig used for both staging and production:
  - [beacon-test-kubeconfig.yaml](C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml)
- Kubernetes context name: `beacon-test`

Typical environment setup:

```powershell
$env:KUBECONFIG="C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml"
```

## Helm Chart And Values

Important: the real chart used for staging and production is not the repo-local `helm\beacon-crm` folder. The live deploys use the external chart under Downloads.

### Actual chart used

- Chart root:
  - [gtm chart](C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm)
- Chart manifest:
  - [Chart.yaml](C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm\Chart.yaml)

### Environment values

- Staging values:
  - [gtm.yaml](C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm.yaml)
- Production values:
  - [gtm-prod.yaml](C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm-prod.yaml)

### Helm binary path used on this machine

- `C:\Users\sarthu\AppData\Local\Microsoft\WinGet\Packages\Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe\windows-amd64\helm.exe`

## Runtime Architecture

The deployed stack has these workloads:

- `backend`: FastAPI application
- `frontend`: React + Vite app served by Nginx
- `worker`: Celery worker for enrichment, syncs, AI tasks, etc.
- `beat`: Celery beat / scheduler
- `priority-worker`: high-priority Celery queue worker
- `postgresql`: application database
- `redis`: Celery broker and cache

Typical running pods are:

- `gtm-backend-deployment-*`
- `gtm-frontend-deployment-*`
- `gtm-worker-deployment-*`
- `gtm-beat-deployment-*`
- `gtm-priority-worker-deployment-*`
- `gtm-postgresql-0`
- `gtm-redis-master-0`

## Repo Structure

### Top level

```text
C:\gtm-prototype
├─ .claude
├─ alembic
├─ app
├─ frontend
├─ helm
├─ scripts
├─ tmp
├─ .env
├─ alembic.ini
├─ docker-compose.yml
├─ Dockerfile
├─ requirements.txt
└─ DEPLOYMENT_HANDOFF.md
```

### Backend

```text
app
├─ api
│  └─ v1
│     └─ endpoints
├─ clients
├─ core
├─ models
├─ repositories
├─ schemas
├─ services
└─ tasks
```

Important backend folders:

- `app/api/v1/endpoints`: REST API routes
- `app/clients`: external integrations like Gmail, Google Calendar, tl;dv, Aircall
- `app/models`: SQLModel entities
- `app/repositories`: DB query logic
- `app/services`: orchestration and business logic
- `app/tasks`: Celery background jobs

### Frontend

```text
frontend\src
├─ components
├─ lib
├─ pages
└─ types
```

Important frontend folders:

- `frontend/src/pages`: top-level app pages
- `frontend/src/components`: reusable UI
- `frontend/src/lib`: API clients, helpers, auth, toast system
- `frontend/src/types`: TypeScript types

## Key Files For Deployment And Debugging

### Build and runtime

- Backend Dockerfile:
  - [Dockerfile](C:\gtm-prototype\Dockerfile)
- Frontend Dockerfile:
  - [frontend/Dockerfile](C:\gtm-prototype\frontend\Dockerfile)
- Local compose stack:
  - [docker-compose.yml](C:\gtm-prototype\docker-compose.yml)

### Database migrations

- Alembic config:
  - [alembic.ini](C:\gtm-prototype\alembic.ini)
- Migration folder:
  - [alembic/versions](C:\gtm-prototype\alembic\versions)

### Personal inbox and calendar sync

- Personal sync endpoints:
  - [personal_email_sync.py](C:\gtm-prototype\app\api\v1\endpoints\personal_email_sync.py)
- Shared inbox endpoints:
  - [email_sync.py](C:\gtm-prototype\app\api\v1\endpoints\email_sync.py)
- Personal sync Celery tasks:
  - [personal_email_sync.py](C:\gtm-prototype\app\tasks\personal_email_sync.py)
- Personal sync service:
  - [personal_email_sync.py](C:\gtm-prototype\app\services\personal_email_sync.py)
- Calendar client:
  - [google_calendar.py](C:\gtm-prototype\app\clients\google_calendar.py)
- Calendar sync service:
  - [calendar_sync.py](C:\gtm-prototype\app\services\calendar_sync.py)
- Gmail OAuth helpers:
  - [gmail_oauth.py](C:\gtm-prototype\app\services\gmail_oauth.py)
- User token store model:
  - [user_email_connection.py](C:\gtm-prototype\app\models\user_email_connection.py)

### Meetings and pre-meeting

- Meetings endpoints:
  - [meetings.py](C:\gtm-prototype\app\api\v1\endpoints\meetings.py)
- Meeting model:
  - [meeting.py](C:\gtm-prototype\app\models\meeting.py)
- Pre-meeting UI:
  - [PreMeetingAssistance.tsx](C:\gtm-prototype\frontend\src\pages\PreMeetingAssistance.tsx)
- Settings page:
  - [Settings.tsx](C:\gtm-prototype\frontend\src\pages\Settings.tsx)

### Account sourcing

- Account sourcing company detail:
  - [AccountSourcingCompanyDetail.tsx](C:\gtm-prototype\frontend\src\pages\AccountSourcingCompanyDetail.tsx)
- Account sourcing contact detail:
  - [AccountSourcingContactDetail.tsx](C:\gtm-prototype\frontend\src\pages\AccountSourcingContactDetail.tsx)
- Frontend API layer:
  - [api.ts](C:\gtm-prototype\frontend\src\lib\api.ts)

### Analytics and pipeline

- Sales analytics API:
  - [analytics.py](C:\gtm-prototype\app\api\v1\endpoints\analytics.py)
- Sales analytics page:
  - [SalesAnalytics.tsx](C:\gtm-prototype\frontend\src\pages\SalesAnalytics.tsx)
- Pipeline page:
  - [Pipeline.tsx](C:\gtm-prototype\frontend\src\pages\Pipeline.tsx)
- Deal drawer:
  - [DealDetailDrawer.tsx](C:\gtm-prototype\frontend\src\components\deal\DealDetailDrawer.tsx)

### Worker and scheduling

- Celery app / schedule:
  - [celery_app.py](C:\gtm-prototype\app\celery_app.py)
- tl;dv sync task:
  - [tldv_sync.py](C:\gtm-prototype\app\tasks\tldv_sync.py)

## How Images Are Built

### Backend

Run from repo root:

```powershell
cd C:\gtm-prototype
docker buildx build --platform linux/amd64 . -t beacon.azurecr.io/gtm-be:<TAG> --push --builder builder
```

### Frontend

Run from the frontend folder:

```powershell
cd C:\gtm-prototype\frontend
docker buildx build --platform linux/amd64 . -t beacon.azurecr.io/gtm-fe:<TAG> --build-arg VITE_API_URL= --push --builder builder
```

Notes:

- Backend must be built from repo root because it uses the root [Dockerfile](C:\gtm-prototype\Dockerfile).
- Frontend must be built from [frontend](C:\gtm-prototype\frontend) because it has its own Dockerfile and Vite build context.
- Frontend is usually built with empty `VITE_API_URL` so it uses relative URLs and the same image can work in staging and prod.

## How Deployments Are Performed

### Staging deploy pattern

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe\windows-amd64\helm.exe" upgrade --install gtm C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm -n gtm --create-namespace -f C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm.yaml --set-string backend.image=beacon.azurecr.io/gtm-be:<BACKEND_TAG> --set-string frontend.image=beacon.azurecr.io/gtm-fe:<FRONTEND_TAG> --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml
```

### Production deploy pattern

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe\windows-amd64\helm.exe" upgrade --install gtm C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm -n gtm-prod --create-namespace -f C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm-prod.yaml --set-string backend.image=beacon.azurecr.io/gtm-be:<BACKEND_TAG> --set-string frontend.image=beacon.azurecr.io/gtm-fe:<FRONTEND_TAG> --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml
```

## How To Verify A Deploy

### Staging

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm rollout status deploy/gtm-backend-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm rollout status deploy/gtm-frontend-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm rollout status deploy/gtm-worker-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm get pods
curl.exe -I https://gtm.staging2.beacon.li/
```

### Production

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod rollout status deploy/gtm-backend-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod rollout status deploy/gtm-frontend-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod rollout status deploy/gtm-worker-deployment
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod get pods
curl.exe -I https://gtm.beacon.li/
```

## Common Debugging Commands

### Pods

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm get pods
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod get pods
```

### Worker logs

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm logs deploy/gtm-worker-deployment --since=20m
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod logs deploy/gtm-worker-deployment --since=20m
```

### Backend logs

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm logs deploy/gtm-backend-deployment --since=20m
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod logs deploy/gtm-backend-deployment --since=20m
```

### Database inspection via Postgres pod

```powershell
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod exec pod/gtm-postgresql-0 -- env
kubectl --kubeconfig C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml -n gtm-prod exec pod/gtm-postgresql-0 -- env PGPASSWORD=<DB_PASSWORD> psql -U beacon -d beacon -c "<SQL>"
```

## Known Deployment Nuances

### Important

- The repo-local chart under `helm\beacon-crm` is not the chart used for live deploys.
- Production and staging can intentionally run different backend and frontend SHAs.
- Backend, worker, beat, and priority-worker generally move together on the backend image tag.
- Frontend can move independently.

### Personal inbox and calendar sync

Current state of the production investigation:

- Worker/runtime instability for personal sync was fixed and deployed.
- The calendar path no longer trusts only stored scope metadata; it now attempts the real Google Calendar API.
- For users like `rakesh@beacon.li`, the live production Calendar API call still returns `403 Forbidden`.
- For `sarthak@beacon.li`, stored scopes include Gmail, Calendar, and Drive, but the live Calendar API still returned `403 Forbidden` and no Google Calendar meetings were created.
- Production currently has `0` rows in `meetings` where `external_source = 'google_calendar'`.
- Conclusion: the app-side task path is fixed enough to attempt the API, but Google is denying Calendar in production. This likely needs Google OAuth app / consent configuration verification for the production client.

Relevant production Google client:

- OAuth client ID:
  - `436209550782-0jdrtg1cucvoqd0c192hni8sggdv6q7r.apps.googleusercontent.com`

## Recent Commits Relevant To Deployment

- `49cee30` — analytics: milestone summary cards, poc_agreed milestone, calendar date range filter
- `7965ed2` — pipeline funnel to board header, deal source required, prospect contact gaps + TS fixes
- `53ed9a0` — tighten prospect hygiene filters
- `b20810c` — stabilize company select search
- `e1bfdb4` — fallback to real Calendar API auth checks
- `0643a84` — inline domain editing for sourced accounts

## Safe Handoff Summary For Another AI

If another AI needs to continue deployment or debugging work:

1. Start from this file.
2. Use the kubeconfig at [beacon-test-kubeconfig.yaml](C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml).
3. Deploy with the external Helm chart under [gtm chart](C:\Users\sarthu\Downloads\gtm-helm\gtm-helm\gtm).
4. Treat backend and frontend images as independently deployable.
5. For personal Gmail/Calendar issues, inspect:
   - [personal_email_sync.py](C:\gtm-prototype\app\tasks\personal_email_sync.py)
   - [google_calendar.py](C:\gtm-prototype\app\clients\google_calendar.py)
   - [calendar_sync.py](C:\gtm-prototype\app\services\calendar_sync.py)
   - [gmail_oauth.py](C:\gtm-prototype\app\services\gmail_oauth.py)
6. If meetings still do not import in prod, check:
   - `user_email_connections.token_data.scopes`
   - live worker logs for `google_calendar`
   - whether Google Calendar API returns `403`
   - whether the production Google OAuth client/consent screen actually grants Calendar and Drive access


Use this exact flow in PowerShell.

1. Set shared variables.

```powershell
$TAG="v0.12-$(git rev-parse --short HEAD)"
$KCFG="C:\\gtm-prototype\\tmp\\beacon-test-kubeconfig.yaml"
$HELM="$env:LOCALAPPDATA\\Microsoft\\WinGet\\Packages\\Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe\\windows-amd64\\helm.exe"
$CHART="C:\\Users\\sarthu\\Downloads\\gtm-helm\\gtm-helm\\gtm"
$STAGING_VALUES="C:\\Users\\sarthu\\Downloads\\gtm-helm\\gtm-helm\\gtm.yaml"
$PROD_VALUES="C:\\Users\\sarthu\\Downloads\\gtm-helm\\gtm-helm\\gtm-prod.yaml"
$env:KUBECONFIG=$KCFG
```

1. Log in to ACR. Skip this if you already did it and it still works.

```powershell
$pw = '<ACR_PASSWORD_FROM_SECURE_STORE>'
$pw | docker login beacon.azurecr.io -u codebuild --password-stdin
```

1. Build and push the backend image from the repo root.

```powershell
cd C:\\gtm-prototype
docker buildx build --platform linux/amd64 . -t beacon.azurecr.io/gtm-be:$TAG --push --builder builder
```

1. Build and push the frontend image from the `frontend` folder.
Use empty `VITE_API_URL` so the same frontend image works in both staging and prod.

```powershell
cd C:\\gtm-prototype\\frontend
docker buildx build --platform linux/amd64 . -t beacon.azurecr.io/gtm-fe:$TAG --build-arg VITE_API_URL= --push --builder builder
```

1. Deploy to staging.

```powershell
& $HELM upgrade --install gtm $CHART `
  -n gtm `
  --create-namespace `
  -f $STAGING_VALUES `
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG `
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG `
  --kubeconfig $KCFG
```

1. Wait for the staging rollout.

```powershell
kubectl --kubeconfig $KCFG -n gtm rollout status deploy/gtm-backend-deployment
kubectl --kubeconfig $KCFG -n gtm rollout status deploy/gtm-frontend-deployment
kubectl --kubeconfig $KCFG -n gtm rollout status deploy/gtm-worker-deployment
kubectl --kubeconfig $KCFG -n gtm rollout status deploy/gtm-beat-deployment
kubectl --kubeconfig $KCFG -n gtm get pods
```

1. Smoke test staging.

```powershell
curl.exe -I <https://gtm.staging2.beacon.li/>
curl.exe -i <https://gtm.staging2.beacon.li/api/v1/auth/google/login>
```

1. If staging looks good in browser and API checks pass, deploy the exact same tag to production.

```powershell
& $HELM upgrade --install gtm $CHART `
  -n gtm-prod `
  --create-namespace `
  -f $PROD_VALUES `
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG `
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG `
  --kubeconfig $KCFG
```

1. Wait for the production rollout.

```powershell
kubectl --kubeconfig $KCFG -n gtm-prod rollout status deploy/gtm-backend-deployment
kubectl --kubeconfig $KCFG -n gtm-prod rollout status deploy/gtm-frontend-deployment
kubectl --kubeconfig $KCFG -n gtm-prod rollout status deploy/gtm-worker-deployment
kubectl --kubeconfig $KCFG -n gtm-prod rollout status deploy/gtm-beat-deployment
kubectl --kubeconfig $KCFG -n gtm-prod get pods
```

1. Smoke test production.

```powershell
curl.exe -I <https://gtm.beacon.li/>
curl.exe -i <https://gtm.beacon.li/api/v1/auth/google/login>
```

A couple of important rules while doing this:

- Run backend build from `C:\\gtm-prototype`, not `frontend`.
- Run frontend build from `C:\\gtm-prototype\\frontend`.
- Do not deploy prod with a different tag than the one you validated in staging.

If you want, I can also turn this into a single reusable `deploy-staging.ps1` and `deploy-prod.ps1` pair for you.
|


Backup

  Created and ran a full compressed prod DB backup:

  - Path: tmp/prod-db-backups/beacon-prod-20260503-015141.dump
  - Metadata: tmp/prod-db-backups/beacon-prod-20260503-015141.txt
  - Size: 19.41 MB
  - Prod DB logical size was about 55 MB, so this is cheap enough to keep regularly.

  Added backup tooling:

  - scripts/backup_prod_postgres.ps1
      - Uses pg_dump -Fc
      - Keeps latest 14 backups by default
      - Writes to tmp/prod-db-backups
  - docs/PROD_BACKUPS.md
      - Explains backup/restore flow
      - Notes targeted cleanup backups

I added the blob-upload path, but I cannot complete the actual upload from this machine yet because there is no Azure
  access available here:

  - Az PowerShell modules are not installed
  - no PROD_BACKUP_BLOB_CONTAINER_SAS_URL env var exists
  - no storage connection string/key is present in repo/env

  What I did implement:

  - scripts/upload_backup_to_blob.ps1
  - Updated scripts/backup_prod_postgres.ps1 so future backups auto-upload if PROD_BACKUP_BLOB_CONTAINER_SAS_URL is set.
  - Updated docs/PROD_BACKUPS.md with upload and retention guidance.

  To finish the upload, provide a private Azure Blob container SAS URL with write/create permissions, then run:

  $env:PROD_BACKUP_BLOB_CONTAINER_SAS_URL = "https://<account>.blob.core.windows.net/<container>?<sas>"
  powershell -ExecutionPolicy Bypass -File .\scripts\upload_backup_to_blob.ps1 `
    -ContainerSasUrl $env:PROD_BACKUP_BLOB_CONTAINER_SAS_URL `
    -Files @(
      "C:\gtm-prototype\tmp\prod-db-backups\beacon-prod-20260503-015141.dump",
      "C:\gtm-prototype\tmp\prod-db-backups\beacon-prod-20260503-015141.txt"
    )

  After that, every run of backup_prod_postgres.ps1 will upload automatically.