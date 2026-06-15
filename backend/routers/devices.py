"""
Device Management CRUD endpoints.

GET    /devices                      - list all devices without passwords
POST   /devices                      - create a new device
PUT    /devices/{id}                 - update a device
DELETE /devices/{id}                 - delete a device and its inventory
POST   /devices/{id}/test-connection - test F5 connectivity
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_password, encrypt_password
from ..database import get_db
from ..schemas import DeviceCreate, DeviceOut, DeviceTopologyConfig, DeviceUpdate
from ..services.f5_client import F5Client

router = APIRouter(prefix="/devices", tags=["devices"])
logger = logging.getLogger(__name__)


@router.get("", response_model=List[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    """List all devices without returning passwords."""
    result = await db.execute(
        text("SELECT * FROM devices ORDER BY name ASC")
    )
    rows = result.mappings().all()
    return [DeviceOut.model_validate(dict(row)) for row in rows]


@router.get("/topology-config/by-hostname/{hostname}", response_model=DeviceTopologyConfig)
async def get_topology_config_by_hostname(
    hostname: str,
    db: AsyncSession = Depends(get_db),
):
    """Return topology login config by hostname, name, or management IP."""
    res = await db.execute(
        text(
            """
            SELECT *
            FROM devices
            WHERE hostname = :hostname
               OR name = :hostname
               OR management_ip = :hostname
            ORDER BY enabled DESC, name ASC
            LIMIT 1
            """
        ),
        {"hostname": hostname},
    )
    device = res.mappings().first()
    if not device:
        raise HTTPException(
            status_code=404,
            detail=f'Device with hostname "{hostname}" was not found',
        )

    try:
        password = decrypt_password(device["password_encrypted"])
    except Exception as e:
        logger.warning(
            f"Password decrypt failed for device id={device['id']} ({device['name']}): {e}. "
            "SECRET_KEY may differ from the one used when the device was added."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Password for device '{device['name']}' could not be decrypted "
                f"(SECRET_KEY may have changed). "
                f"Edit the device in Devices and re-enter the password."
            ),
        )

    return DeviceTopologyConfig(
        id=device["id"],
        name=device["name"],
        hostname=device["hostname"],
        management_ip=device["management_ip"],
        username=device["username"],
        password=password,
        verify_ssl=bool(device["verify_ssl"]),
    )


@router.post("", response_model=DeviceOut, status_code=201)
async def create_device(body: DeviceCreate, db: AsyncSession = Depends(get_db)):
    """Create a new device and encrypt its password."""
    encrypted = encrypt_password(body.password)
    result = await db.execute(
        text(
            """
            INSERT INTO devices (name, management_ip, username, password_encrypted, verify_ssl, enabled, last_status)
            VALUES (:name, :management_ip, :username, :password_encrypted, :verify_ssl, :enabled, 'NEVER')
            RETURNING *
            """
        ),
        {
            "name": body.name,
            "management_ip": body.management_ip,
            "username": body.username,
            "password_encrypted": encrypted,
            "verify_ssl": int(body.verify_ssl),
            "enabled": int(body.enabled),
        },
    )
    await db.commit()
    row = result.mappings().first()
    return DeviceOut.model_validate(dict(row))


@router.put("/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: int,
    body: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a device. Empty passwords keep the existing password."""
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    existing = res.mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")

    name = body.name if body.name is not None else existing["name"]
    mgmt_ip = body.management_ip if body.management_ip is not None else existing["management_ip"]
    username = body.username if body.username is not None else existing["username"]
    verify_ssl = body.verify_ssl if body.verify_ssl is not None else bool(existing["verify_ssl"])
    enabled = body.enabled if body.enabled is not None else bool(existing["enabled"])

    if body.password:
        password_encrypted = encrypt_password(body.password)
    else:
        password_encrypted = existing["password_encrypted"]

    result = await db.execute(
        text(
            """
            UPDATE devices
            SET name = :name,
                management_ip = :management_ip,
                username = :username,
                password_encrypted = :password_encrypted,
                verify_ssl = :verify_ssl,
                enabled = :enabled
            WHERE id = :id
            RETURNING *
            """
        ),
        {
            "name": name,
            "management_ip": mgmt_ip,
            "username": username,
            "password_encrypted": password_encrypted,
            "verify_ssl": int(verify_ssl),
            "enabled": int(enabled),
            "id": device_id,
        },
    )
    await db.commit()
    row = result.mappings().first()
    return DeviceOut.model_validate(dict(row))


@router.post("/bulk-update-password")
async def bulk_update_password(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update the password for all devices or for selected device IDs."""
    password: str = body.get("password", "")
    device_ids: Optional[List[int]] = body.get("device_ids")

    if not password:
        raise HTTPException(status_code=400, detail="Password cannot be empty")

    encrypted = encrypt_password(password)

    if device_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(device_ids)))
        params = {f"id{i}": did for i, did in enumerate(device_ids)}
        params["enc"] = encrypted
        result = await db.execute(
            text(f"UPDATE devices SET password_encrypted = :enc WHERE id IN ({placeholders}) RETURNING id"),
            params,
        )
    else:
        result = await db.execute(
            text("UPDATE devices SET password_encrypted = :enc RETURNING id"),
            {"enc": encrypted},
        )

    updated_ids = [row[0] for row in result.fetchall()]
    await db.commit()

    logger.info(f"Bulk update password: {len(updated_ids)} devices updated (ids={updated_ids})")
    return {"ok": True, "updated": len(updated_ids), "device_ids": updated_ids}


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a device and the inventory owned by that device."""
    res = await db.execute(
        text("SELECT id, name, management_ip, hostname FROM devices WHERE id = :id"),
        {"id": device_id},
    )
    device = res.mappings().first()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.execute(
        text(
            """
            DELETE FROM inventory_ip
            WHERE device_id = :id
               OR hostname IN (:hostname, :name, :management_ip)
            """
        ),
        {
            "id": device_id,
            "hostname": device["hostname"] or "",
            "name": device["name"] or "",
            "management_ip": device["management_ip"] or "",
        },
    )

    await db.execute(
        text("DELETE FROM devices WHERE id = :id"),
        {"id": device_id},
    )

    await db.commit()


@router.post("/{device_id}/test-connection")
async def test_device_connection(device_id: int, db: AsyncSession = Depends(get_db)):
    """Test F5 connectivity for one saved device."""
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    device = res.mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.commit()

    try:
        password = decrypt_password(device["password_encrypted"])
    except Exception as e:
        logger.warning(
            f"Password decrypt failed for device id={device_id}: {e}. "
            "Ensure backend/.env SECRET_KEY matches the key used when the device was saved."
        )
        return {
            "ok": False,
            "error": (
                f"Failed to decrypt password. Ensure SECRET_KEY in backend/.env has not changed "
                f"since this device was added. Detail: {e}"
            ),
        }

    client = F5Client(
        host=device["management_ip"],
        username=device["username"],
        password=password,
        verify_ssl=bool(device["verify_ssl"]),
        timeout=10.0,
    )
    result = await client.test_connection()
    return result
