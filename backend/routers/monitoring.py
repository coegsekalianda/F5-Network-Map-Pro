from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.monitoring_service import (
    get_batch_vs_connection_stats,
    get_vs_connection_stats,
    list_virtual_servers_for_device,
)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


class MonitorTarget(BaseModel):
    device_id: int
    partition: str = "Common"
    vs_name: str


class MonitorBatchRequest(BaseModel):
    targets: List[MonitorTarget]


@router.get("/virtual-servers")
async def monitoring_virtual_servers(
    device_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await list_virtual_servers_for_device(db, device_id)


@router.get("/vs-connections")
async def monitoring_vs_connections(
    device_id: int = Query(...),
    partition: str = Query("Common"),
    vs_name: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await get_vs_connection_stats(db, device_id, partition, vs_name)


@router.post("/vs-connections/batch")
async def monitoring_vs_connections_batch(
    body: MonitorBatchRequest,
    db: AsyncSession = Depends(get_db),
):
    targets = [target.model_dump() for target in body.targets]
    return {"results": await get_batch_vs_connection_stats(db, targets)}
