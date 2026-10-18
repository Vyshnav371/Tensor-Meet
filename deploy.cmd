@echo off
rem Deploy Tensor to Cloud Run (asia-south1): public URL, app-level sign-in, scales to zero.
rem Run from this folder after: gcloud auth login
setlocal
set P=ai-builder-nexusforge
set SA=tensor-run@%P%.iam.gserviceaccount.com
cd /d "%~dp0"

echo [1/3] Runtime service account with only the roles Tensor needs...
call gcloud iam service-accounts describe %SA% --project %P% >nul 2>&1 || call gcloud iam service-accounts create tensor-run --display-name "Tensor Cloud Run runtime" --project %P% || exit /b 1
for %%R in (roles/aiplatform.user roles/speech.client roles/datastore.user roles/documentai.apiUser roles/discoveryengine.editor roles/bigquery.dataEditor roles/bigquery.jobUser roles/logging.logWriter) do (
  call gcloud projects add-iam-policy-binding %P% --member serviceAccount:%SA% --role %%R --condition=None --quiet >nul || exit /b 1
)
call gcloud storage buckets add-iam-policy-binding gs://ai-builder-nexusforge-tensor --member serviceAccount:%SA% --role roles/storage.objectAdmin >nul || exit /b 1

rem Real Gmail sending, once infra\gmail_auth.py has stored the authorization in Secret Manager; otherwise sandbox.
set GMAIL=--clear-secrets
call gcloud secrets describe tensor-gmail-refresh-token --project %P% >nul 2>&1
if not errorlevel 1 (
  for %%S in (tensor-gmail-client-id tensor-gmail-client-secret tensor-gmail-refresh-token tensor-gmail-sender) do (
    call gcloud secrets add-iam-policy-binding %%S --project %P% --member serviceAccount:%SA% --role roles/secretmanager.secretAccessor >nul || exit /b 1
  )
  set GMAIL=--set-secrets TENSOR_GMAIL_CLIENT_ID=tensor-gmail-client-id:latest,TENSOR_GMAIL_CLIENT_SECRET=tensor-gmail-client-secret:latest,TENSOR_GMAIL_REFRESH_TOKEN=tensor-gmail-refresh-token:latest,TENSOR_GMAIL_SENDER=tensor-gmail-sender:latest
  echo Gmail: live
) else (
  echo Gmail: sandbox ^(run infra\gmail_auth.py to send real email^)
)

echo [2/3] Building and deploying (about 5 minutes)...
rem max-instances 1: a meeting's live audio state lives in one process. timeout 3600: long WebSocket meetings.
call gcloud run deploy tensor --source . --region asia-south1 --project %P% ^
  --service-account %SA% --allow-unauthenticated ^
  --min-instances 0 --max-instances 1 --concurrency 80 --cpu 2 --memory 2Gi --no-cpu-throttling ^
  --timeout 3600 --session-affinity ^
  --set-env-vars TENSOR_GCP_PROJECT=%P%,TENSOR_COOKIE_SECURE=1,TENSOR_MAX_COMPANIES=25 %GMAIL% || exit /b 1

echo [3/3] Done. Your URL:
call gcloud run services describe tensor --region asia-south1 --project %P% --format "value(status.url)"
