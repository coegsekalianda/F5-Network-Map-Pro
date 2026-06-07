"""
routers/devices.py — CRUD endpoints untuk Device Management.

GET    /devices              — list semua device (tanpa password)
POST   /devices              — tambah device baru
PUT    /devices/{id}         — update device (password kosong = tidak diubah)
DELETE /devices/{id}         — hapus device
POST   /devices/{id}/test-connection — test koneksi ke F5
"""
import logging
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..crypto import encrypt_password, decrypt_password
from ..schemas import DeviceCreate, DeviceUpdate, DeviceOut, DeviceTopologyConfig
from ..services.f5_client import F5Client

router = APIRouter(prefix="/devices", tags=["devices"])
logger = logging.getLogger(__name__)


@router.get("", response_model=List[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    """List semua device — password tidak dikembalikan."""
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
    """Ambil config login topology berdasarkan hostname, name, atau management IP."""
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
            detail=f'Device dengan hostname "{hostname}" tidak ditemukan',
        )

    try:
        password = decrypt_password(device["password_encrypted"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal decrypt password: {e}")

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
    """Tambah device baru. Password akan dienkripsi."""
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
    """Update device. Jika password kosong/tidak diisi, password lama tetap dipakai."""
    # Ambil device existing
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    existing = res.mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Device tidak ditemukan")

    # Tentukan nilai baru (fallback ke nilai lama)
    name        = body.name        if body.name        is not None else existing["name"]
    mgmt_ip     = body.management_ip if body.management_ip is not None else existing["management_ip"]
    username    = body.username    if body.username    is not None else existing["username"]
    verify_ssl  = body.verify_ssl  if body.verify_ssl  is not None else bool(existing["verify_ssl"])
    enabled     = body.enabled     if body.enabled     is not None else bool(existing["enabled"])

    # Password: hanya ganti jika ada isian baru
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


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """Hapus device dari database."""
    res = await db.execute(
        text("SELECT id FROM devices WHERE id = :id"), {"id": device_id}
    )
    if not res.mappings().first():
        raise HTTPException(status_code=404, detail="Device tidak ditemukan")

    await db.execute(text("DELETE FROM devices WHERE id = :id"), {"id": device_id})
    await db.commit()


@router.post("/{device_id}/test-connection")
async def test_device_connection(device_id: int, db: AsyncSession = Depends(get_db)):
    """Test koneksi ke F5 untuk device tertentu."""
    res = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"), {"id": device_id}
    )
    device = res.mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device tidak ditemukan")

    try:
        password = decrypt_password(device["password_encrypted"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal decrypt password: {e}")

    client = F5Client(
        host=device["management_ip"],
        username=device["username"],
        password=password,
        verify_ssl=bool(device["verify_ssl"]),
        timeout=10.0,
    )
    result = await client.test_connection()
    return result
