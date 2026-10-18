@echo off
rem Tensor, local mode: one URL (http://localhost:8000) for the app and API.
rem Requires: gcloud auth application-default login (Gemini, Speech, Firestore, Document AI, Vertex AI Search, BigQuery).
setlocal
cd /d "%~dp0"

if not exist backend\.venv\Scripts\python.exe (
  echo Creating Python environment...
  python -m venv backend\.venv || exit /b 1
  backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt || exit /b 1
)
if not exist frontend\dist\tensor-ui\browser\index.html (
  echo Building the frontend...
  pushd frontend
  if not exist node_modules call npm.cmd ci || exit /b 1
  call npm.cmd run build || exit /b 1
  popd
)

set TENSOR_STATIC_DIR=%~dp0frontend\dist\tensor-ui\browser
start "" http://localhost:8000/
cd backend
.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
