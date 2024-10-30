from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Dict
from enum import Enum
import decimal
import time
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, condecimal

app = FastAPI()


class EventStatus(str, Enum):
    NEW = "NEW"
    FINISHED_WIN = "FINISHED_WIN"
    FINISHED_LOSE = "FINISHED_LOSE"


class Event(BaseModel):
    event_id: str
    coefficient: condecimal(gt=0, max_digits=10, decimal_places=2)
    deadline: int
    status: EventStatus

    class Config:
        json_encoders = {
            decimal.Decimal: lambda v: float(v)
        }


events: Dict[str, Event] = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/events")
async def get_events():
    return [jsonable_encoder(event) for event in events.values()]


@app.get("/event/{event_id}")
async def get_event(event_id: str):
    if event_id in events:
        return jsonable_encoder(events[event_id])
    else:
        raise HTTPException(status_code=404, detail="Событие не найдено")


@app.post("/event")
async def create_event(event: Event):
    if event.event_id in events:
        raise HTTPException(status_code=400, detail="Событие уже существует")
    events[event.event_id] = event
    return {"message": "Событие создано"}


@app.put("/event/{event_id}")
async def update_event(event_id: str, event: Event):
    if event_id not in events:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    events[event_id] = event
    return {"message": "Событие обновлено"}


# Инициализация с несколькими событиями
@app.on_event("startup")
async def startup_event():
    e1 = Event(
        event_id="1",
        coefficient=decimal.Decimal("1.20"),
        deadline=int(time.time()) + 600,
        status=EventStatus.NEW
    )
    events[e1.event_id] = e1
    e2 = Event(
        event_id="2",
        coefficient=decimal.Decimal("1.50"),
        deadline=int(time.time()) + 1200,
        status=EventStatus.NEW
    )
    events[e2.event_id] = e2
