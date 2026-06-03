# NigeriaVoteWatch — Claude Session Context

## What this project is

NigeriaVoteWatch is a Nigerian election integrity monitoring platform. It scrapes INEC (Independent National Electoral Commission) results in real time, cross-references them with citizen observer reports, flags statistical anomalies, and presents everything on an interactive map. The primary integrity mechanism is a **cryptographic hash chain** on the results collection — every new result extends the chain so any tampering is detectable.

## Tech stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.12, FastAPI, uvicorn |
| Database | MongoDB (Beanie ODM + Motor async driver; Atlas free tier in prod) |
| Task scheduler | APScheduler (AsyncIOScheduler, Africa/Lagos tz) |
| IReV scraper | Playwright (Chromium headless) + httpx (image download) |
| INEC scraper | Playwright + BeautifulSoup4 |
| OCR | pytesseract (Tesseract) + Pillow; Google Vision API fallback |
| Frontend | React 18, Vite 6, React Router 6 |
| Charts | Recharts |
| Map | Leaflet.js + react-leaflet (choropleth, Nigeria GeoJSON) |
| Mobile PWA | Same React app, manifest.json, offline report queue (localStorage) |
| Data fetching | TanStack Query (React Query) |
| State | Zustand |
| Containerisation | Docker Compose (backend + frontend only; DB is Atlas) |

## Folder structure

```
nigeriavoterwatch/
├── backend/
│   ├── app/
│   │   ├── main.py               FastAPI app + lifespan
│   │   ├── config.py             Pydantic-settings (reads .env)
│   │   ├── database.py           Motor client + init_beanie()
│   │   ├── models/
│   │   │   ├── elections.py      State, LGA, Ward, PollingUnit, Election
│   │   │   ├── results.py        ElectionResult (append-only), ResultDocument
│   │   │   ├── audit.py          AuditLog (write-once), HashChainEntry
│   │   │   ├── observers.py      Observer, ObserverReport, Anomaly
│   │   │   └── scrape_log.py     IRevScrapeRun, ScrapeLogEntry
│   │   ├── schemas/
│   │   │   ├── common.py         PaginatedResponse[T], to_oid(), not_found()
│   │   │   ├── elections.py      ElectionCreate/Read, StateRead, LGARead, …
│   │   │   ├── results.py        ResultRead (with geo fields), ElectionSummaryRead
│   │   │   ├── observers.py      ObserverRegister, ReportSubmit, …
│   │   │   └── anomalies.py      AnomalyRead, AnomalyResolve
│   │   ├── routers/
│   │   │   ├── elections.py      CRUD + geography hierarchy endpoints
│   │   │   ├── results.py        Paginated results + election summary
│   │   │   ├── observers.py      Invite flow, registration, field reports
│   │   │   ├── anomalies.py      List anomalies, mark resolved
│   │   │   └── stats.py          upload-by-state, scrape-history, anomaly-summary
│   │   ├── scrapers/
│   │   │   ├── inec_scraper.py   Playwright + BS4 INEC portal scraper
│   │   │   └── irev_scraper.py   IReV image scraper with EC8A hash chain
│   │   ├── services/
│   │   │   └── anomaly_detector.py  7 integrity checks (C1–C7); runs post-scrape
│   │   ├── ocr/
│   │   │   ├── __init__.py       extract_text_best_effort()
│   │   │   ├── tesseract_processor.py
│   │   │   └── vision_processor.py
│   │   ├── tasks/
│   │   │   ├── scheduler.py      APScheduler setup/teardown
│   │   │   ├── scraping_tasks.py scrape_inec_results() cron job (5 min)
│   │   │   ├── irev_tasks.py     scrape_irev_results() + anomaly detection (5 min)
│   │   │   └── integrity_tasks.py verify_hash_chain() cron job (15 min)
│   │   └── utils/
│   │       └── hash_chain.py     Hash chain primitives
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── public/
│   │   ├── index.html            Leaflet CSS linked here
│   │   ├── manifest.json         PWA manifest (Nigeria green #008751)
│   │   └── nigeria-states.geojson  37-feature GeoJSON for choropleth map
│   └── src/
│       ├── App.jsx               Routes + Nav
│       ├── index.jsx             ReactDOM root + QueryClient
│       ├── index.css             Global styles + shared component CSS
│       ├── pages/
│       │   ├── Home.jsx          Dashboard stat cards
│       │   ├── Results.jsx       ElectionSelector + paginated DataGrid + geo filters
│       │   ├── MapPage.jsx       Leaflet choropleth (upload % by state) + sidebar
│       │   ├── Reports.jsx       Observer PWA — GPS, photos, offline queue
│       │   └── Analytics.jsx     Recharts: upload BarChart, scrape LineChart, anomaly Scatter
│       └── services/
│           └── api.js            Axios instance + typed API functions for all endpoints
├── docker/
│   ├── docker-compose.yml        backend + frontend only (MongoDB is Atlas)
│   ├── Dockerfile.backend        Python + Tesseract + Playwright
│   └── Dockerfile.frontend       Vite build → nginx
├── scripts/
│   ├── setup_db.sh               Activate venv, install deps, run seed
│   └── seed_nigeria.py           Seed 37 states, 774 LGAs, ~8,609 wards
├── .env.example
└── CLAUDE.md                     ← this file
```

## API surface (`/api/…`)

| Prefix | Routes |
|---|---|
| `/elections` | `GET/POST /`, `GET /{id}`, `PATCH /{id}/status` |
| `/elections/geography` | `/states`, `/states/{id}/lgas`, `/lgas/{id}/wards`, `/wards/{id}/polling-units`, `/polling-units/by-code/{code}` |
| `/results` | `GET /` (paginated, multi-filter), `GET /election/{id}/summary`, `GET /{id}` |
| `/observers` | `POST /invite`, `POST /register`, `GET/POST /reports`, `GET /reports/{id}` |
| `/anomalies` | `GET /` (filter by severity/type/state/resolved), `GET /{id}`, `PATCH /{id}/resolve` |
| `/stats` | `GET /upload-by-state`, `GET /scrape-history`, `GET /anomaly-summary` |
| `/health` | `GET /api/health` |

## Database design decisions

### MongoDB + Beanie ODM

All models are Beanie `Document` subclasses. No SQL, no migrations. Schema evolution
is done by adding optional fields with defaults. Collections:

| Collection | Notes |
|---|---|
| `states` | 37 documents (seeded) |
| `lgas` | 774 documents (seeded) |
| `wards` | ~8,609 documents (seeded) |
| `polling_units` | ~176,000 — imported from INEC data |
| `elections` | One document per election event |
| `election_results` | **Append-only** — never updated or deleted |
| `result_documents` | EC8A scanned PDFs/images with OCR output |
| `audit_log` | **Write-once** — every significant system action |
| `hash_chain_entries` | Mirror of chain for fast O(n) verification |
| `observers` | Registered field observers |
| `observer_reports` | Field reports submitted by observers |
| `anomalies` | Flagged integrity issues (7 types, 3 severities) |
| `irev_scrape_runs` | One document per 5-min IReV scrape cycle |
| `scrape_log` | One document per polling unit per scrape run |

### Append-only results

`ElectionResult` documents are **never updated or deleted**. To correct a result,
insert a new document with `supersedes_id` pointing to the old one. Queries for
the current result filter `{supersedes_id: null, status: {$ne: "superseded"}}`.

### Hash chain

Every `ElectionResult` carries:
- `content_hash` — SHA-256 of the canonical JSON payload (sorted keys, no whitespace)
- `prev_hash` — `block_hash` of the previous chain entry (genesis = `SHA-256(GENESIS_BLOCK_SEED)`)
- `chain_sequence` — monotonically increasing integer (unique index)

`HashChainEntry` is a denormalised mirror for fast O(n) chain verification without
re-reading the full results collection. `verify_hash_chain` runs every 15 min and
writes the result to `AuditLog`.

### EC8A image chain

IReV scrape entries carry a separate image hash chain:
- `image_hash_sha256` — SHA-256 of the raw EC8A image bytes
- `image_block_hash` = `SHA-256(image_hash + prev_image_block_hash)`
- Genesis: `SHA-256(GENESIS_BLOCK_SEED + ":irev-images")`

Any polling unit whose `image_hash_sha256` changes across scrape runs triggers
a **CRITICAL** `IMAGE_HASH_CHANGED` anomaly.

### AuditLog

Every significant action writes a row to `AuditLog`. Documents are never modified.
`AuditAction` enum covers all system events, giving a forensic trail independent
of the hash chain.

## Anomaly detection (7 checks, C1–C7)

Implemented in `app/services/anomaly_detector.py`. Runs automatically after every
IReV scrape via `irev_tasks.py`.

| Code | Type | Severity | Trigger |
|---|---|---|---|
| C1 | `VOTE_INFLATION` | CRITICAL | votes_cast > accredited_voters |
| C2 | `OVERCREDITATION` | CRITICAL | accredited_voters > registered_voters |
| C3 | `UNANIMOUS_RESULT` | WARNING | one candidate gets 100% with ≥2 parties |
| C4 | `LATE_RESULT` | WARNING | image absent > 2h after polls close (14:30 WAT) |
| C5 | `TURNOUT_ANOMALY` | WARNING | turnout z-score > 3σ vs LGA mean (min 4 PUs) |
| C6 | `IMAGE_HASH_CHANGED` | CRITICAL | >1 distinct image hash for same PU |
| C7 | `RESULT_DISCREPANCY` | WARNING | OCR vs portal total deviation > 20% |

Deduplication: `(election_id, polling_unit_id, anomaly_type)` triplet is only
flagged once while an anomaly remains unresolved.

## Key invariants

1. **Never** update or delete documents in `election_results`, `audit_log`, or `hash_chain_entries`.
2. `chain_sequence` on `ElectionResult` must match its `HashChainEntry.sequence`.
3. The IReV and INEC scrapers each run at most one concurrent instance (`max_instances=1`).
4. OCR tries Google Vision first (if `GOOGLE_APPLICATION_CREDENTIALS` is set), falls back to Tesseract if confidence < 0.6.
5. All timestamps are stored in UTC; convert to Africa/Lagos (UTC+1) only at the display layer.
6. `GENESIS_BLOCK_SEED` must never change after the first result is inserted — it would invalidate the entire hash chain.

## Nigeria geographic hierarchy

```
37 entities  (36 states + FCT)
  └── 774 LGAs
        └── ~8,609 wards  (seeded)
              └── ~176,000 Polling Units  (imported from INEC)
```

INEC polling unit codes: `{STATE_CODE}/{LGA_CODE}/{WARD_CODE}/{PU_SEQ}` (all 2-digit zero-padded, except PU_SEQ which is 3-digit).

## Running locally

```bash
# 1. Copy and fill in .env
cp .env.example .env
# Set MONGODB_URI to your Atlas connection string

# 2. Set up backend (venv + deps + seed DB)
bash scripts/setup_db.sh

# 3. Backend dev server
cd backend
uvicorn app.main:app --reload

# 4. Install Playwright browsers (for scrapers)
cd backend && playwright install chromium

# 5. Frontend
cd frontend
npm install
npm run dev
```

## Environment variables

See `.env.example` for the full list. Critical ones:

| Variable | Purpose |
|---|---|
| `MONGODB_URI` | Atlas SRV connection string (or `mongodb://localhost:27017` for local) |
| `MONGODB_DB_NAME` | Database name (default: `nigeriavoterwatch`) |
| `GENESIS_BLOCK_SEED` | Salt for hash chain genesis — **never change after first result** |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON for Vision OCR |
| `IREV_BASE_URL` | Base URL of INEC IReV portal |
| `INEC_RESULTS_BASE_URL` | Base URL of INEC results portal |
| `JWT_SECRET_KEY` | Sign access tokens — rotate carefully |
| `OBSERVER_INVITE_CODE_TTL_HOURS` | Default 48 — how long invite codes stay valid |

## What's next to build

In priority order:

1. **PollingUnit importer** — parse official INEC ward-to-PU CSV/PDF and bulk-insert ~176,000 `PollingUnit` documents linked to seeded wards
2. **JWT authentication middleware** — replace `X-Observer-Id` header stub with signed tokens; protect admin endpoints
3. **WebSocket live updates** — push new `ElectionResult` and `Anomaly` documents to connected dashboards via FastAPI WebSocket
4. **Home dashboard** — wire stat cards to real counts from DB (results collected, active observers, open anomalies)
5. **GeoJSON accuracy** — replace the approximated hexagonal `nigeria-states.geojson` with high-resolution boundaries from geoBoundaries or GADM (download when network is available)
6. **Alembic-style seeding for PollingUnits** — import from INEC's published PU register (CSV format, ~176k rows)
7. **Observer mobile app refinements** — compress photos before upload, show PU on map, push notifications for nearby incidents
8. **Rate limiting + API keys** — add per-IP rate limiting and API key auth for the public read endpoints
