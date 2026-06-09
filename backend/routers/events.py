"""
即時事件 API - PTT 社群爬蟲資料
"""
from fastapi import APIRouter
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
        json.dump({"events": events, "updated_at": datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2)

@router.get("/")
def get_events(include_deleted: bool = False, confirmed_only: bool = False):
    events = load_events()
    if not include_deleted:
        events = [e for e in events if e.get("status") != "deleted"]
    if confirmed_only:
        events = [e for e in events if e.get("status") == "confirmed"]
    return {"events": events, "count": len(events)}

@router.post("/")
def add_event(event: Event):
    events = load_events()
    new_event = event.dict()
    new_event["id"] = int(datetime.now().timestamp() * 1000)  # 用時間戳避免重複 id
    new_event["status"] = "pending"
    new_event["crawled_at"] = new_event.get("crawled_at") or datetime.now().isoformat()
    events.append(new_event)
    save_events(events)
    return {"status": "ok", "id": new_event["id"]}

@router.post("/confirm/{event_id}")
def confirm_event(event_id: int):
    events = load_events()
    for e in events:
        if e.get("id") == event_id:
            e["status"] = "confirmed"
    save_events(events)
    return {"status": "ok"}

@router.delete("/{event_id}")
def delete_event(event_id: int):
    events = load_events()
    for e in events:
        if e.get("id") == event_id:
            e["status"] = "deleted"
    save_events(events)
    return {"status": "ok"}

@router.post("/restore/{event_id}")
def restore_event(event_id: int):
    events = load_events()
    for e in events:
        if e.get("id") == event_id:
            e["status"] = "pending"
    save_events(events)
    return {"status": "ok"}