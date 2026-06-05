from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import json, os

from routers import route, events, construction

# ── 啟動時載入 risk_data.json ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    data_path = os.path.join(os.path.dirname(__file__), "data", "risk_data.json")
    try:
        with open(data_path, encoding="utf-8") as f:
            app.state.risk_data = json.load(f)
        print(f"[OK] 載入 {len(app.state.risk_data['nodes'])} 個路口")
    except FileNotFoundError:
        print("[WARN] data/risk_data.json 不存在，請放入後重啟")
        app.state.risk_data = {"nodes": [], "features": [], "model_metrics": {}}
    yield

app = FastAPI(
    title="桃園市車禍風險預測平台 API",
    description="提供安全路線規劃、即時事件、施工排程等服務",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS（允許 GitHub Pages 前端和本地開發）────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://610e00.github.io",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由 ──────────────────────────────────────────────────────────
app.include_router(route.router,        prefix="/api/route",        tags=["路線規劃"])
app.include_router(events.router,       prefix="/api/events",       tags=["即時事件"])
app.include_router(construction.router, prefix="/api/construction",  tags=["施工排程"])

@app.get("/", tags=["健康檢查"])
def root():
    return {"status": "ok", "message": "桃園市車禍風險預測平台 API 運作中"}

@app.get("/health", tags=["健康檢查"])
def health():
    return {"status": "ok"}
