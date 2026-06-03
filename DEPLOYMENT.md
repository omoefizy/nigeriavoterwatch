# Deployment Guide

This guide walks through deploying NigeriaVoteWatch to production:
**Railway** (backend) + **Vercel** (frontend) + **MongoDB Atlas** (database).

---

## 1. MongoDB Atlas

1. Go to [cloud.mongodb.com](https://cloud.mongodb.com) → **Create a free cluster** (M0, any region close to your Railway region — `eu-west` or `us-east`).
2. **Database Access** → Add a database user with *Read and write to any database*. Save the username and password.
3. **Network Access** → Add IP `0.0.0.0/0` (allow all) for Railway's dynamic IPs.
4. **Connect** → *Drivers* → *Python* → copy the SRV connection string:
   ```
   mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   ```
5. Test the connection locally:
   ```bash
   python3 -c "import motor.motor_asyncio; c = motor.motor_asyncio.AsyncIOMotorClient('<uri>'); print('ok')"
   ```
6. Seed the database (run once):
   ```bash
   MONGODB_URI="<uri>" bash scripts/setup_db.sh
   ```

---

## 2. Railway (backend)

### 2.1 Create a project

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Connect your GitHub account and select the `nigeriavoterwatch` repository.
3. Railway will auto-detect the project. When prompted for a **service root**, set it to `backend/`.
4. Railway picks up `backend/railway.toml` automatically and builds using the `backend/Dockerfile`.

### 2.2 Set environment variables

In the Railway dashboard → your service → **Variables**, add every variable below.

> Variables marked **required** will cause startup failures if omitted.

| Variable | Required | Description |
|---|---|---|
| `MONGODB_URI` | ✅ | Atlas SRV connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (e.g. `nigeriavoterwatch`) |
| `GENESIS_BLOCK_SEED` | ✅ | Long random string. **Never change** after first result. |
| `JWT_SECRET_KEY` | ✅ | Long random string for signing access tokens |
| `APP_SECRET_KEY` | ✅ | Long random string for the admin bootstrap endpoint |
| `ALLOWED_ORIGINS` | ✅ | Comma-separated list of allowed CORS origins, e.g. `https://nigeriavoterwatch.vercel.app` |
| `APP_ENV` | ✅ | `production` |
| `APP_DEBUG` | | `false` |
| `SENTRY_DSN` | | Sentry project DSN for error tracking |
| `IREV_BASE_URL` | | `https://inecelectionresults.ng` |
| `INEC_RESULTS_BASE_URL` | | `https://results.inecnigeria.org` |
| `GOOGLE_APPLICATION_CREDENTIALS` | | Path to service account JSON (mounted volume) |
| `S3_BUCKET` | | S3-compatible bucket name for EC8A image storage |
| `S3_ACCESS_KEY` | | S3 access key |
| `S3_SECRET_KEY` | | S3 secret key |
| `S3_REGION` | | e.g. `af-south-1` |
| `LOG_LEVEL` | | `INFO` (default) |

Generate secrets with:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2.3 Verify deployment

After Railway finishes building (3–5 minutes on first deploy):

```bash
# Replace with your Railway public URL
BACKEND=https://nigeriavoterwatch-production.up.railway.app

curl $BACKEND/api/health
# → {"status":"ok","env":"production"}

curl "$BACKEND/api/elections/geography/states" | python3 -m json.tool | head -20
# → first few of 37 Nigerian states
```

### 2.4 Bootstrap the admin account (first time only)

```bash
curl -s -X POST "$BACKEND/api/auth/setup" \
  -H "Content-Type: application/json" \
  -d '{
    "setup_secret": "<APP_SECRET_KEY>",
    "email": "admin@yourorg.ng",
    "password": "<strong-password>",
    "full_name": "NVW Admin"
  }' | python3 -m json.tool
```

Store the returned `access_token` — you'll need it to create observer invite codes.

### 2.5 Note the Railway service name

In Railway dashboard → your service → **Settings** → note the **Service Name** (e.g. `backend`). You'll need it for GitHub Actions.

---

## 3. Vercel (frontend)

### 3.1 Create a project

1. Go to [vercel.com](https://vercel.com) → **Add New Project** → import the same repository.
2. Set **Root Directory** to `frontend`.
3. Framework: **Vite** (auto-detected).
4. Build command: `npm run build` (from `vercel.json`).
5. Output directory: `dist` (from `vercel.json`).

### 3.2 Set environment variables

In the Vercel dashboard → **Settings** → **Environment Variables**:

| Variable | Value | Environment |
|---|---|---|
| `VITE_API_URL` | Your Railway backend URL, e.g. `https://nigeriavoterwatch-production.up.railway.app` | Production |

> `VITE_API_URL` is baked into the frontend bundle at build time. The SPA calls the Railway API directly; Vercel handles routing only.

### 3.3 Verify deployment

After Vercel finishes (~1 minute):
- Open your Vercel URL → Dashboard should load, stat cards should show data.
- Navigate to **Map** — choropleth of 37 states should render.
- Check browser devtools Network tab: API calls should hit your Railway domain.

---

## 4. GitHub Actions secrets

Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add each secret:

| Secret name | Where to get it |
|---|---|
| `RAILWAY_TOKEN` | Railway dashboard → **Account Settings** → **Tokens** → Create token |
| `RAILWAY_SERVICE_NAME` | Railway service name from §2.5, e.g. `backend` |
| `VERCEL_TOKEN` | Vercel dashboard → **Account Settings** → **Tokens** → Create token |
| `VERCEL_ORG_ID` | Run `npx vercel whoami --token <token>` or check `.vercel/project.json` after `vercel link` |
| `VERCEL_PROJECT_ID` | Same source as `VERCEL_ORG_ID` |
| `GENESIS_BLOCK_SEED` | Same value set in Railway — used by CI hash chain tests |

To retrieve `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`:
```bash
cd frontend
npx vercel link          # follow prompts, links to your Vercel project
cat .vercel/project.json # → { "orgId": "...", "projectId": "..." }
```

### Pipeline behaviour

| Event | Jobs that run |
|---|---|
| Push to **any branch** | `hash-chain` → `test` |
| Push to **main** | `hash-chain` → `test` → `deploy-backend` + `deploy-frontend` (parallel) |
| Pull request to main | `hash-chain` → `test` |

---

## 5. Production environment variables — full reference

Copy this into Railway's **Raw Editor** and fill in all values.

```bash
# ── Application ────────────────────────────────────────────────────────────────
APP_ENV=production
APP_SECRET_KEY=                        # generate: python3 -c "import secrets; print(secrets.token_hex(32))"
APP_DEBUG=false
APP_HOST=0.0.0.0

# ── CORS (add your Vercel domain) ──────────────────────────────────────────────
ALLOWED_ORIGINS=https://nigeriavoterwatch.vercel.app

# ── MongoDB Atlas ──────────────────────────────────────────────────────────────
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=nigeriavoterwatch

# ── Integrity (CRITICAL — never change GENESIS_BLOCK_SEED after first result) ──
GENESIS_BLOCK_SEED=                    # generate as above
CHAIN_VERIFICATION_INTERVAL_MINUTES=15

# ── JWT ────────────────────────────────────────────────────────────────────────
JWT_SECRET_KEY=                        # generate as above
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ── Observer portal ────────────────────────────────────────────────────────────
OBSERVER_INVITE_CODE_TTL_HOURS=48
OBSERVER_REPORT_RATE_LIMIT=10

# ── INEC scrapers ──────────────────────────────────────────────────────────────
INEC_RESULTS_BASE_URL=https://results.inecnigeria.org
IREV_BASE_URL=https://inecelectionresults.ng
SCRAPER_INTERVAL_SECONDS=300
IREV_SCRAPER_INTERVAL_SECONDS=300
SCRAPER_CONCURRENCY=4
IREV_SCRAPER_CONCURRENCY=2

# ── OCR ────────────────────────────────────────────────────────────────────────
TESSERACT_CMD=/usr/bin/tesseract
OCR_LANGUAGE=eng
# Optional: Google Vision API for higher-accuracy OCR fallback
GOOGLE_APPLICATION_CREDENTIALS=
GOOGLE_CLOUD_PROJECT=

# ── Storage (S3-compatible — leave blank to use local disk) ───────────────────
S3_BUCKET=nigeriavoterwatch-docs
S3_REGION=af-south-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_ENDPOINT_URL=

# ── Monitoring ─────────────────────────────────────────────────────────────────
SENTRY_DSN=
LOG_LEVEL=INFO
```

---

## 6. Post-deployment checklist

- [ ] `GET /api/health` returns `{"status":"ok","env":"production"}`
- [ ] `GET /api/elections/geography/states` returns 37 states
- [ ] Admin account bootstrapped via `/api/auth/setup`
- [ ] Frontend loads at Vercel URL; stat cards show data
- [ ] Map renders choropleth with Nigeria state boundaries
- [ ] WebSocket badge on Dashboard shows **Live** (green)
- [ ] Hash chain CI check passes on the main branch
- [ ] GitHub Actions secrets all set; a push to main triggers both deploys

---

## 7. Rollback

**Backend (Railway):** Dashboard → **Deployments** → select a previous deployment → **Rollback**.

**Frontend (Vercel):** Dashboard → **Deployments** → select a previous deployment → **Promote to Production**.

**Database:** Results are append-only. To revert seeding, drop the `states`, `lgas`, and `wards` collections and re-run `scripts/seed_nigeria.py`.
