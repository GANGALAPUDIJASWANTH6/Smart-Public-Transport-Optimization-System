import os
import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://transit:transit@localhost:5432/transit")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(16), default="#65d9b1")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Ridership(Base):
    __tablename__ = "ridership_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route_id: Mapped[str] = mapped_column(String(16), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    passengers: Mapped[int] = mapped_column(Integer)
    weather: Mapped[str] = mapped_column(String(24), default="clear")
    event: Mapped[bool] = mapped_column(Boolean, default=False)
    delay_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)


class ObservationIn(BaseModel):
    route_id: str = Field(min_length=1, max_length=16)
    observed_at: datetime
    passengers: int = Field(ge=0, le=10000)
    weather: str = Field(default="clear", max_length=24)
    event: bool = False
    delay_minutes: float | None = Field(default=None, ge=0, le=360)

    @field_validator("observed_at")
    @classmethod
    def timestamp_must_include_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone, for example 2026-10-07T08:00:00Z")
        if value > datetime.now(timezone.utc) + timedelta(minutes=15):
            raise ValueError("observed_at cannot be more than 15 minutes in the future")
        return value


class ForecastIn(BaseModel):
    route_id: str
    target_at: datetime
    weather: str = "clear"
    event: bool = False

    @field_validator("target_at")
    @classmethod
    def target_must_include_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("target_at must include a timezone")
        return value


app = FastAPI(title="TransitIQ API", version="1.0.0", description="Public transport operations and ridership forecasting API")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def cached(key: str):
    try:
        import redis
        client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=0.15, socket_timeout=0.15, decode_responses=True)
        value = client.get(key)
        return json.loads(value) if value else None
    except Exception:
        return None


def cache(key: str, value, seconds: int = 20):
    try:
        import redis
        client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=0.15, socket_timeout=0.15)
        client.setex(key, seconds, json.dumps(value, default=str))
    except Exception:
        pass


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    with Session() as db:
        routes = [
            Route(id="R12", name="Central — Riverside", color="#65d9b1"),
            Route(id="R08", name="University — Midtown", color="#8c8bff"),
            Route(id="R21", name="Airport — Central", color="#ffb66d"),
            Route(id="R04", name="North Park — Market", color="#f28eae"),
            Route(id="R16", name="Harbor — Tech District", color="#63bdf3"),
        ]
        for route in routes:
            if not db.get(Route, route.id):
                db.add(route)
        db.commit()


@app.get("/health")
def health():
    with Session() as db:
        db.execute(select(Route.id).limit(1))
    return {"status": "ok", "service": "transitiq-api"}


@app.get("/api/routes")
def get_routes():
    if value := cached("routes"):
        return value
    with Session() as db:
        routes = db.scalars(select(Route).where(Route.active.is_(True)).order_by(Route.id)).all()
        result = []
        for route in routes:
            latest = db.scalar(select(Ridership).where(Ridership.route_id == route.id).order_by(Ridership.observed_at.desc()).limit(1))
            delay = latest.delay_minutes if latest else None
            if latest is None:
                status = "Awaiting telemetry"
            elif (datetime.now(timezone.utc) - latest.observed_at).total_seconds() > 900:
                status = "Telemetry stale"
            else:
                status = "No delay data" if delay is None else ("Delayed" if delay > 5 else "On time")
            result.append({"id": route.id, "name": route.name, "color": route.color, "status": status, "occupancy": None, "eta": None, "buses": None, "last_observed_at": latest.observed_at if latest else None})
        cache("routes", result, 60)
        return result


@app.get("/api/alerts")
def get_alerts():
    with Session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = db.scalars(select(Ridership).where(Ridership.delay_minutes > 10, Ridership.observed_at >= cutoff).order_by(Ridership.observed_at.desc()).limit(20)).all()
        routes = {r.id: r.name for r in db.scalars(select(Route)).all()}
        now = datetime.now(timezone.utc)
        return [{"severity": "high", "title": "Significant delay reported", "detail": f"{r.route_id} · {routes.get(r.route_id, 'Unknown route')} · {r.delay_minutes:g} min reported delay", "time": f"{max(0, int((now - r.observed_at).total_seconds() / 60))} min ago"} for r in rows]


@app.get("/api/dashboard/summary")
def dashboard_summary():
    if value := cached("dashboard:summary"):
        return value
    with Session() as db:
        routes = db.scalars(select(Route).where(Route.active.is_(True))).all()
        rows = db.scalars(select(Ridership).order_by(Ridership.observed_at.desc()).limit(2000)).all()
        now = datetime.now(timezone.utc)
        today = [r for r in rows if r.observed_at.date() == now.date()]
        delays = [r.delay_minutes for r in rows if r.delay_minutes is not None]
        result = {
            "active_routes": len(routes), "active_vehicles": 0,
            "on_time_rate": round(100 * sum(d <= 5 for d in delays) / len(delays), 1) if delays else None,
            "avg_delay": round(sum(delays) / len(delays), 1) if delays else None,
            "ridership_today": sum(r.passengers for r in today), "delay_alerts": sum(d > 10 for d in delays),
            "observation_count": len(rows),
            "data_quality": "observed" if len(rows) else "no_observations",
        }
        cache("dashboard:summary", result, 15)
        return result


@app.get("/api/demand")
def demand(route_id: str | None = None):
    key = f"demand:{route_id or 'network'}"
    if value := cached(key):
        return value
    with Session() as db:
        stmt = select(Ridership).order_by(Ridership.observed_at.desc()).limit(2000)
        if route_id:
            stmt = stmt.where(Ridership.route_id == route_id)
        rows = list(reversed(db.scalars(stmt).all()))
        hourly = [0] * 12
        counts = [0] * 12
        for row in rows:
            bucket = max(0, min(11, row.observed_at.hour - 6))
            hourly[bucket] += row.passengers
            counts[bucket] += 1
        result = {"values": [round(hourly[i] / counts[i]) if counts[i] else 0 for i in range(12)], "source": "observed_history", "observations": len(rows)}
        cache(key, result, 30)
        return result


@app.post("/api/ridership", status_code=201)
def add_observation(observation: ObservationIn):
    with Session() as db:
        if not db.get(Route, observation.route_id):
            raise HTTPException(404, "Unknown route_id")
        row = Ridership(**observation.model_dump())
        db.add(row)
        db.commit()
        try:
            import redis
            client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=0.15, socket_timeout=0.15)
            client.delete("dashboard:summary")
            client.delete("routes")
            for key in client.scan_iter("demand:*"):
                client.delete(key)
        except Exception:
            pass
        return {"id": row.id, "status": "recorded"}


def features(rows):
    import numpy as np
    from sklearn.preprocessing import LabelEncoder
    routes = LabelEncoder()
    route_values = routes.fit_transform([r.route_id for r in rows])
    weather_map = {"clear": 0, "cloudy": 1, "rain": 2, "storm": 3, "snow": 4}
    x = np.array([[route_values[i], r.observed_at.hour, r.observed_at.weekday(), r.observed_at.month, weather_map.get(r.weather.lower(), 1), int(r.event)] for i, r in enumerate(rows)], dtype=float)
    y = np.array([r.passengers for r in rows], dtype=float)
    return x, y, routes


@app.post("/api/forecast")
def forecast(request: ForecastIn):
    from sklearn.ensemble import RandomForestRegressor
    with Session() as db:
        if not db.get(Route, request.route_id):
            raise HTTPException(404, "Unknown route_id")
        rows = db.scalars(select(Ridership).order_by(Ridership.observed_at)).all()
    matching = [r for r in rows if r.route_id == request.route_id and r.observed_at.hour == request.target_at.hour and r.observed_at.weekday() == request.target_at.weekday()]
    if len(rows) < 100 or len(matching) < 3:
        baseline = matching or [r for r in rows if r.route_id == request.route_id and abs(r.observed_at.hour - request.target_at.hour) <= 1]
        prediction = round(sum(r.passengers for r in baseline) / len(baseline)) if baseline else None
        return {"route_id": request.route_id, "target_at": request.target_at, "predicted_passengers": prediction,
                "method": "historical_baseline", "training_observations": len(rows),
                "message": "Collect at least 100 observations and 3 comparable time slots to enable ML forecasting."}

    x, y, encoder = features(rows)
    split = max(int(len(rows) * .8), 1)
    model = RandomForestRegressor(n_estimators=250, min_samples_leaf=2, max_features=1.0, random_state=42, n_jobs=-1)
    model.fit(x[:split], y[:split])
    validation = model.predict(x[split:]) if split < len(rows) else []
    errors = [abs(float(p) - float(actual)) for p, actual in zip(validation, y[split:])]
    target_route = encoder.transform([request.route_id])[0]
    weather_map = {"clear": 0, "cloudy": 1, "rain": 2, "storm": 3, "snow": 4}
    target = [[target_route, request.target_at.hour, request.target_at.weekday(), request.target_at.month, weather_map.get(request.weather.lower(), 1), int(request.event)]]
    return {"route_id": request.route_id, "target_at": request.target_at, "predicted_passengers": max(0, round(float(model.predict(target)[0]))),
            "method": "random_forest", "training_observations": len(rows),
            "validation_mae": round(sum(errors) / len(errors), 2) if errors else None, "validation_samples": len(errors),
            "validation_method": "chronological 80/20 holdout", "feature_importance": {"route": round(float(model.feature_importances_[0]), 3), "hour": round(float(model.feature_importances_[1]), 3), "weekday": round(float(model.feature_importances_[2]), 3), "month": round(float(model.feature_importances_[3]), 3), "weather": round(float(model.feature_importances_[4]), 3), "event": round(float(model.feature_importances_[5]), 3)}}
