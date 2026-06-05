"""
即時事件 API
爬蟲組員完成後，把資料寫入 data/events.json 即可自動反映
格式參考 Section 8.2 of VibeCoding spec
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Literal, Optional
import json, os
from datetime import datetime

router = APIRouter()
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "events.json")

class Event(BaseModel):
    type:        Literal["accident", "construction", "congestion", "other"]
    description: str
    lat:         float
    lng:         float
    confidence:  float = 1.0
    source_url:  Optional[str] = None
    crawled_at:  Optional[str] = None

def load_events():
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except FileNotFoundError:
        return []

def save_events(events: list):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"events": events, "updated_at": datetime.now().isoformat()}, f,
                  ensure_ascii=False, indent=2)

@router.get("/")
def get_events():
    """取得所有即時事件（confidence >= 0.6 為確認，< 0.6 標示待確認）"""
    events = load_events()
    for e in events:
        e["confirmed"] = e.get("confidence", 1.0) >= 0.6
    return {"events": events, "count": len(events)}

@router.post("/")
def add_event(event: Event):
    """新增即時事件（爬蟲模組呼叫）"""
    events = load_events()
    new_event = event.dict()
    new_event["id"] = len(events) + 1
    new_event["crawled_at"] = new_event.get("crawled_at") or datetime.now().isoformat()
    events.append(new_event)
    save_events(events)
    return {"status": "ok", "id": new_event["id"]}

@router.delete("/{event_id}")
def delete_event(event_id: int):
    """刪除事件（管理後台確認事件已排除）"""
    events = [e for e in load_events() if e.get("id") != event_id]
    save_events(events)
    return {"status": "ok"}
