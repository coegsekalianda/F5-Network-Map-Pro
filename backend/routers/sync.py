"""
Inventory sync endpoints for saved F5 devices.

POST /sync/all         - sync all enabled devices
POST /sync/device/{id} - sync one device by ID
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Device
from ..schemas import SyncDeviceResult, SyncResult
from ..services.sync_service import sync_all_devices, sync_one_device

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)


@router.post("/all", response_model=SyncResult)
async def sync_all(db: AsyncSession = Depends(get_db)):
    """
    Sync all enabled F5 devices.
    Runs up to five devices concurrently.
    Device failures do not stop the rest of the sync process.
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
    """Sync one F5 device by ID."""
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")

    device = Device(
        id=row["id"],
        name=row["name"],
        management_ip=row["management_ip"],
        username=row["username"],
        password_encrypted=row["password_encrypted"],
        verify_ssl=bool(row["verify_ssl"]),
        enabled=bool(row["enabled"]),
    )

    await db.execute(
        text("UPDATE devices SET last_status = 'SYNCING', last_error = NULL WHERE id = :id"),
        {"id": device_id},
    )
    # Commit early to release the read transaction before the slow network sync.
    await db.commit()

    result = await sync_one_device(db, device)
    return result
