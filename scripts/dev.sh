#!/usr/bin/env bash
# Start the backend dev server (run from project root)
set -euo pipefail
cd "$(dirname "$0")/.."
source backend/venv/bin/activate
cd backend
export $(grep -v '^#' ../.env | xargs)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
