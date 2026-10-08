# TransitIQ — Smart Public Transport Optimization System

Full-stack transit operations dashboard built with React, FastAPI, PostgreSQL, Redis, and Python machine learning. The dashboard tracks route operations, passenger demand, vehicle load, and service alerts. The API accepts ridership observations and produces route-level demand forecasts.

## Repository contents

```text
.
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   └── transit.py
│   ├── uploads/
│   ├── .env.example
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── api.js
│   │   ├── index.css
│   │   └── main.jsx
│   ├── index.html
│   ├── package.json
│   └── package-lock.json
├── docker-compose.yml
└── README.md
```

## Start the complete stack

Install Docker Desktop, then run from the project root:

```bash
docker compose up --build
```

- Dashboard: http://localhost:5173
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

The UI starts with clearly labeled sample data when the API is unavailable. The API seeds route metadata only; it does not seed fabricated ridership history. Empty or sparse history is reported as such.

## Local development

Start PostgreSQL and Redis (or use the Docker services), then run the API:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Copy backend/.env.example to backend/.env and adjust settings for local services
uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Copy `backend/.env.example` to `backend/.env` and set `DATABASE_URL`, `REDIS_URL`, and `CORS_ORIGINS` for local services as needed. Redis is used for short-lived dashboard caching; the application continues to work if Redis is unavailable.

## Ridership ingestion

Record an observation for a known route. Use UTC timestamps (with `Z`) for consistent time features:

```bash
curl -X POST http://localhost:8000/api/ridership \
  -H "Content-Type: application/json" \
  -d '{"route_id":"R12","observed_at":"2026-10-07T08:00:00Z","passengers":74,"weather":"clear","event":false,"delay_minutes":2}'
```

Integrate AVL/GPS, ticketing or passenger counters, and a reliable weather/event feed to supply real observations. Protect ingestion endpoints with authentication, rate limits, and an authorized network before deploying beyond a trusted development environment.

## Forecasting and accuracy

`POST /api/forecast` takes `route_id`, `target_at`, and optional `weather` and `event` fields. It uses a comparable-hour historical baseline until it has at least 100 observations overall and 3 matching route/time samples. With sufficient history it trains a Random Forest and reports mean absolute error (MAE) on a chronological 80/20 holdout.

There is no honest universal accuracy percentage: forecast quality depends on representative, correctly timestamped local data, service disruptions, weather, events, and upstream sensor quality. MAE and validation sample count are returned so operators can judge measured performance. Retraining is currently performed on request; for production, move training to a scheduled job and persist/version the validated model.

## API

- `GET /health`
- `GET /api/dashboard/summary`
- `GET /api/routes`
- `GET /api/alerts`
- `GET /api/demand?route_id=R12`
- `POST /api/ridership`
- `POST /api/forecast`

## Push to GitHub

Create an empty GitHub repository. If you extracted `TransitIQ-GitHub.zip` into its own folder, open a terminal there and run:

```bash
git init -b main
git add -A
git commit -m "Prepare TransitIQ transport optimization system"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the URL with the new repository URL. If you’re pushing from this existing workspace instead, use `git remote set-url origin ...` because it already has a Git remote. The ignore rules exclude `.env` files, local databases, Python virtual environments, `node_modules`, and build output. Keep `backend/.env.example` in the repository; never add your actual `.env` file or credentials.

## Production work still needed

This is a runnable project foundation, not a deployment-ready public transit control system. Connect a real vehicle telemetry feed and dispatch/alert rules; add user authentication and roles, audit logs, migrations, ingestion validation and deduplication, observability, backups, and deployment secrets before operational use. Do not use dashboard demo values for service decisions.
