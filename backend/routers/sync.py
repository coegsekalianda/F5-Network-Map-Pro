"""
routers/sync.py — Endpoint sync inventory dari F5 devices.

POST /sync/all           — sync semua device yang enabled
POST /sync/device/{id}   — sync satu device by ID
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Device
from ..schemas import SyncResult, SyncDeviceResult
from ..services.sync_service import sync_all_devices, sync_one_device
from ..crypto import decrypt_password

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)


@router.post("/all", response_model=SyncResult)
async def sync_all(db: AsyncSession = Depends(get_db)):
    """
    Sync semua device F5 yang enabled.
    Concurrent 5 device sekaligus.
    Device yang gagal tidak menghentikan proses sync lainnya.
    """
    logger.info("Starting Sync All...")
    result = await sync_all_devices(db)
    logger.info(
        f"Sync All done: {result.success}/{result.total_devices} success, "
        f"{result.vs_ip_synced} VS, {result.pool_member_ip_synced} POOL_MEMBER, "
        f"{result.self_ip_synced} SELF_IP"
    )
    return result


@router.post("/device/{device_id}", response_model=SyncDeviceResult)
async def sync_device_by_id(
    device_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Sync satu device F5 by ID."""
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Device tidak ditemukan")

    device = Device(
        id=row["id"],
        name=row["name"],
        management_ip=row["management_ip"],
        username=row["username"],
        password_encrypted=row["password_encrypted"],
        verify_ssl=bool(row["verify_ssl"]),
        enabled=bool(row["enabled"]),
    )

    result = await sync_one_device(db, device)
    return result
