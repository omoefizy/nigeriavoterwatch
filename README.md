# NigeriaVoteWatch

Real-time Nigerian election integrity monitoring platform. Scrapes INEC (Independent National Electoral Commission) results, cross-references citizen observer reports, detects statistical anomalies, and chains every result cryptographically so tampering is detectable.

## Architecture

| Layer | Technology |
|---|---|
| Backend API | Python 3.12, FastAPI, uvicorn |
| Database | MongoDB Atlas (Beanie ODM + Motor async driver) |
| Scheduler | APScheduler — scrape every 5 min, chain verify every 15 min |
| Scrapers | Playwright (Chromium headless) + httpx |
| OCR | Tesseract + Google Vision API fallback |
| Frontend | React 18, Vite 6, React Router 6 |
| Charts | Recharts |
| Map | Leaflet + react-leaflet (choropleth, Nigeria GeoJSON) |
| Real-time | FastAPI WebSocket — `results_update` and `anomaly_alert` events |
| PWA | Offline report queue (localStorage), GPS capture, photo upload |
| Auth | JWT (access + refresh tokens), admin and observer roles |
| Hosting | Vercel (frontend) + Railway (backend) |

## Prerequisites

- Python 3.12
- Node.js 22
- MongoDB Atlas free-tier cluster (or local `mongod`)
- Tesseract OCR: `brew install tesseract` (macOS) or `apt install tesseract-ocr`
- Git

## Local setup

### 1 — Clone and configure environment

```bash
git clone https://github.com/your-org/nigeriavoterwatch.git
cd nigeriavoterwatch
cp .env.example .env
```

Open `.env` and set at minimum:

```
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
GENESIS_BLOCK_SEED=<long-random-string>   # NEVER change after first result is inserted
JWT_SECRET_KEY=<long-random-string>
APP_SECRET_KEY=<long-random-string>
```

### 2 — Backend

```bash
# Creates venv, installs deps, seeds 37 states + 774 LGAs
bash scripts/setup_db.sh

# Start API server (http://localhost:8000)
cd backend
source venv/bin/activate
uvicorn app.main:app --reload
```

API docs available at `http://localhost:8000/api/docs`.

### 3 — Bootstrap the first admin account

```bash
curl -s -X POST http://localhost:8000/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{
    "setup_secret": "<APP_SECRET_KEY from .env>",
    "email": "admin@example.com",
    "password": "StrongPassword1!",
    "full_name": "NVW Admin"
  }'
```

This endpoint returns an access token and is disabled once an admin exists.

### 4 — Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`.

### 5 — Run tests

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

The hash chain tests (`tests/test_hash_chain.py`) run without a live database and serve as a CI gate on every push.

## Project structure

```
nigeriavoterwatch/
├── backend/
│   ├── app/
│   │   ├── main.py               FastAPI app + lifespan
│   │   ├── config.py             Pydantic-settings (reads .env)
│   │   ├── database.py           Motor client + Beanie init
│   │   ├── auth/jwt.py           JWT encode/decode + FastAPI dependencies
│   │   ├── models/               Beanie Documents (elections, results, observers…)
│   │   ├── schemas/              Pydantic I/O models
│   │   ├── routers/              FastAPI routers for each domain
│   │   ├── scrapers/             Playwright INEC + IReV scrapers
│   │   ├── services/             Anomaly detector (7 integrity checks)
│   │   ├── tasks/                APScheduler jobs
│   │   ├── ws/                   WebSocket connection manager + router
│   │   └── utils/hash_chain.py   Cryptographic hash chain primitives
│   ├── tests/
│   │   └── test_hash_chain.py    Hash chain integrity unit tests
│   ├── Dockerfile                Production image (Railway)
│   ├── railway.toml              Railway deployment config
│   ├── Procfile                  Fallback start command
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/                Home, Results, MapPage, Reports, Analytics, Anomalies, Login
│   │   ├── hooks/useResultsSocket.js  WebSocket hook (auto-reconnect)
│   │   └── store/wsStore.js      Zustand store for real-time state
│   ├── public/nigeria-states.geojson  37-state GeoJSON (geoBoundaries CC BY 4.0)
│   └── vercel.json               Vercel build + SPA rewrite config
├── docker/
│   ├── docker-compose.yml        Local Docker Compose (backend + frontend)
│   ├── Dockerfile.backend        Dev image
│   └── Dockerfile.frontend       nginx static build
├── scripts/
│   ├── setup_db.sh               One-command local setup
│   └── seed_nigeria.py           Seeds 37 states, 774 LGAs, ~8,609 wards
├── .github/workflows/deploy.yml  CI/CD pipeline
├── .env.example                  Environment variable reference
├── DEPLOYMENT.md                 Step-by-step Railway + Vercel setup guide
└── CLAUDE.md                     AI assistant context file
```

## Key invariants

1. **Append-only results** — `ElectionResult` documents are never updated or deleted. Corrections use `supersedes_id`.
2. **Hash chain** — Every result carries `content_hash`, `prev_hash`, and `block_hash`. `GENESIS_BLOCK_SEED` must never change after the first result is inserted.
3. **WebSocket events** — `results_update` after every IReV scrape; `anomaly_alert` when CRITICAL anomalies are inserted.
4. **Public reads, authenticated writes** — All `GET` endpoints are public; all `POST`/`PATCH` endpoints require a valid JWT.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step Railway + Vercel setup including all required secrets.

## License

MIT
