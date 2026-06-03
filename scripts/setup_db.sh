#!/usr/bin/env bash
# Run from the project root: bash scripts/setup_db.sh
#
# Activates the backend virtualenv, installs Python dependencies,
# and seeds MongoDB with the Nigeria administrative hierarchy
# (37 states, 774 LGAs, ~8,609 wards).
#
# Prerequisite: MONGODB_URI must be set in .env
# (Atlas free-tier connection string — see docker/docker-compose.yml for details)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
VENV_DIR="${BACKEND_DIR}/venv"

# ── 1. Create / activate virtualenv ───────────────────────────────────────────
if [ ! -d "${VENV_DIR}" ]; then
  echo "▶  Creating Python virtualenv…"
  python3 -m venv "${VENV_DIR}"
fi

echo "▶  Activating virtualenv…"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

# ── 2. Install dependencies ───────────────────────────────────────────────────
echo "▶  Installing Python dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r "${BACKEND_DIR}/requirements.txt"

# Playwright browser (Chromium) needed for IReV scraper
echo "▶  Installing Playwright browsers…"
playwright install chromium --with-deps 2>/dev/null || \
  echo "  (Playwright install skipped — run manually if scraper is needed)"

# ── 3. Seed the database ───────────────────────────────────────────────────────
echo "▶  Seeding Nigeria administrative hierarchy…"
python3 "${SCRIPT_DIR}/seed_nigeria.py" "$@"

echo ""
echo "✓  Setup complete."
echo "   Start the backend: cd backend && uvicorn app.main:app --reload"
