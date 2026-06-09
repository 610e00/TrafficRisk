# 桃園市車禍風險預測平台 — 後端 API

## 目錄結構

```
backend/
├── main.py                  # FastAPI 入口
├── requirements.txt
├── render.yaml              # Render 部署設定
├── routers/
│   ├── route.py             # 安全路線規劃 API
│   ├── routing_engine.py    # 路線演算法核心
│   ├── events.py            # 即時事件 API（爬蟲串接）
│   └── construction.py      # 施工排程 API（政府後台串接）
└── data/
    ├── risk_data.json        # Colab 模型輸出（放這裡）
    ├── events.json           # 爬蟲寫入（自動產生）
    └── construction.json     # 政府後台寫入（自動產生）
```

## 本地啟動

```bash
cd backend
pip install -r requirements.txt

# 把 Colab 產出的 risk_data.json 放到 data/ 目錄
cp ../risk_data.json data/

uvicorn main:app --reload --port 8000
```

瀏覽器開 http://localhost:8000/docs 可以看到所有 API 文件

## API 說明

### POST /api/route — 安全路線規劃

```json
{
  "origin_lat": 24.969,
  "origin_lng": 121.224,
  "dest_lat":   24.990,
  "dest_lng":   121.308,
  "period":     "eve",
  "weather":    "小雨",
  "vehicle_mode": "moto"
}
```

回傳：
```json
{
  "fast": {
    "label": "最快路線",
    "duration_min": 12.3,
    "distance_km": 5.2,
    "high_count": 3,
    "mid_count": 2,
    "risk_nodes": [...]
  },
  "safe": {
    "label": "安全路線",
    "duration_min": 14.1,
    "distance_km": 5.8,
    "high_count": 1,
    "mid_count": 1,
    "risk_nodes": [...]
  },
  "comparison": {
    "same_route": false,
    "extra_minutes": 1.8,
    "saved_high": 2,
    "saved_mid": 1,
    "routes_available": 3
  }
}
```

### GET /api/events — 取得即時事件
### POST /api/events — 新增事件（爬蟲呼叫）
### GET /api/construction — 取得施工排程（預設只回今天有效的）
### POST /api/construction — 新增施工排程（政府後台呼叫）

## 部署到 Render

1. 把整個 `backend/` 推到 GitHub（可以是同一個 repo 的子目錄）
2. Render → New Web Service → 連接 GitHub repo
3. Root Directory 填 `backend`
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. 部署完成後把 API URL 填回前端的 `BACKEND_URL`

## 前端串接

部署後把這行加到 index.html：
```javascript
const BACKEND_URL = 'https://your-app.onrender.com';
```

然後把 `calcRoute` 改成打後端 `/api/route` 即可。
