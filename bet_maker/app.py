import os
from sqlalchemy import select
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List
from enum import Enum
import decimal
import time
import uuid
import asyncio
import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Numeric, Enum as SqlEnum
from pydantic import BaseModel, Field, condecimal

DATABASE_URL = os.getenv("DATABASE_URL",
                         "postgresql+asyncpg://postgres:postgres@db:5432/betdb")

engine = create_async_engine(DATABASE_URL, echo=False)
Base = declarative_base()


class BetStatus(str, Enum):
    PENDING = "PENDING"
    WON = "WON"
    LOST = "LOST"


class Bet(Base):
    __tablename__ = 'bets'

    bet_id = Column(String, primary_key=True, index=True)
    event_id = Column(String, index=True)
    amount = Column(Numeric(10, 2))
    status = Column(SqlEnum(BetStatus), default=BetStatus.PENDING)
    coefficient = Column(Numeric(10, 2))


app = FastAPI()


class BetIn(BaseModel):
    event_id: str
    amount: condecimal(gt=0, max_digits=10, decimal_places=2)


class BetOut(BaseModel):
    bet_id: str
    event_id: str
    amount: decimal.Decimal
    status: BetStatus
    coefficient: decimal.Decimal


line_provider_url = os.getenv("LINE_PROVIDER_URL",
                              "http://line_provider:8000")

# Создаем асинхронную сессию
async_session = sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    # Создаем таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Запуск фоновой задачи для обновления статусов ставок
    asyncio.create_task(update_bet_statuses())


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()


@app.get("/events")
async def get_events():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{line_provider_url}/events") as response:
            if response.status == 200:
                events = await response.json()
                # Фильтрация событий по дедлайну
                current_time = int(time.time())
                available_events = [e for e in events if
                                    e['deadline'] > current_time
                                    and e['status'] == "NEW"]

                return available_events
            else:
                raise HTTPException(status_code=500,
                                    detail="Не удалось получить список событий")


@app.post("/bet")
async def place_bet(bet_in: BetIn):
    # Проверка доступности события
    async with aiohttp.ClientSession() as session:
        async with session.get(
                f"{line_provider_url}/event/{bet_in.event_id}") as response:
            if response.status == 200:
                event = await response.json()
                current_time = int(time.time())
                if event['deadline'] <= current_time:
                    raise HTTPException(status_code=400,
                                        detail="Дедлайн для ставок истёк")
                elif event['status'] != "NEW":
                    raise HTTPException(status_code=400,
                                        detail="Событие уже завершено")
                else:
                    # Получаем текущий коэффициент события
                    coefficient = decimal.Decimal(event['coefficient'])
                    bet_id = str(uuid.uuid4())
                    new_bet = Bet(
                        bet_id=bet_id,
                        event_id=bet_in.event_id,
                        amount=bet_in.amount,
                        coefficient=coefficient,
                        status=BetStatus.PENDING
                    )
                    async with async_session() as session_db:
                        async with session_db.begin():
                            session_db.add(new_bet)
                    return {"bet_id": bet_id}
            else:
                raise HTTPException(status_code=400,
                                    detail="Событие не найдено")


@app.get("/bets", response_model=List[BetOut])
async def get_bets():
    async with async_session() as session_db:
        # Используем ORM запрос вместо Bet.__table__.select()
        result = await session_db.execute(select(Bet))
        bets = result.scalars().all()
        return [BetOut(
            bet_id=bet.bet_id,
            event_id=bet.event_id,
            amount=bet.amount,
            status=bet.status,
            coefficient=bet.coefficient
        ) for bet in bets]


async def update_bet_statuses():
    while True:
        async with async_session() as session_db:
            # Получение всех ожидающих ставок через ORM
            async with session_db.begin():  # Начинаем транзакцию
                result = await session_db.execute(
                    select(Bet).where(Bet.status == BetStatus.PENDING)
                )
                bets = result.scalars().all()

                # Список для ставок, которые нужно обновить
                bets_to_update = []

                for bet in bets:
                    # Получение статуса события из line-provider
                    event_id = bet.event_id
                    async with aiohttp.ClientSession() as session_http:
                        async with session_http.get(
                                f"{line_provider_url}/event/{event_id}") as response:
                            if response.status == 200:
                                event = await response.json()
                                if event['status'] != "NEW":
                                    # Событие завершено, обновляем статус ставки
                                    if event['status'] == "FINISHED_WIN":
                                        bet.status = BetStatus.WON
                                    else:
                                        bet.status = BetStatus.LOST
                                    bets_to_update.append(bet)

                # Обновляем все ставки одной транзакцией
                if bets_to_update:
                    session_db.add_all(bets_to_update)

        # Ждём 10 секунд перед следующим обновлением
        await asyncio.sleep(10)
