"""
routers/inventory.py — Endpoint search & manajemen inventory IP.

GET    /inventory/search?ip=x.x.x.x  — exact match search
GET    /inventory/all                  — semua data inventory
DELETE /inventory/clear                — kosongkan seluruh inventory
"""
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import InventoryIPOut, InventorySearchResult

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/search", response_model=InventorySearchResult)
async def search_inventory(
    ip: str = Query(..., description="IP address exact match"),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari IP di inventory database (exact match).
    Tidak login ke F5, hanya baca database lokal.
    """
    ip = ip.strip()
    result = await db.execute(
        text(
            "SELECT * FROM inventory_ip WHERE ip = :ip ORDER BY hostname, type"
        ),
        {"ip": ip},
    )
    rows = result.mappings().all()
    items = [InventoryIPOut.model_validate(dict(row)) for row in rows]
    return InventorySearchResult(ip=ip, results=items)


@router.get("/all", response_model=List[InventoryIPOut])
async def get_all_inventory(
    device_id: Optional[int] = Query(None, description="Filter by device ID"),
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 2000,
):
    """Ambil data inventory, opsional filter by device_id."""
    if device_id is not None:
        dev_res = await db.execute(
            text("SELECT hostname FROM devices WHERE id = :device_id"),
            {"device_id": device_id}
        )
        dev = dev_res.mappings().first()
        if not dev or not dev["hostname"]:
            return []

        result = await db.execute(
            text(
                "SELECT * FROM inventory_ip WHERE hostname = :hostname ORDER BY type, ip LIMIT :limit OFFSET :skip"
            ),
            {"hostname": dev["hostname"], "limit": limit, "skip": skip},
        )
    else:
        result = await db.execute(
            text(
                "SELECT * FROM inventory_ip ORDER BY hostname, type, ip LIMIT :limit OFFSET :skip"
            ),
            {"limit": limit, "skip": skip},
        )
    rows = result.mappings().all()
    return [InventoryIPOut.model_validate(dict(row)) for row in rows]


@router.delete("/clear", status_code=200)
async def clear_inventory(
    device_id: Optional[int] = Query(None, description="Clear only for specific device ID"),
    db: AsyncSession = Depends(get_db)
):
    """Hapus data dari inventory_ip (semua atau berdasarkan device_id)."""
    if device_id is not None:
        dev_res = await db.execute(
            text("SELECT hostname FROM devices WHERE id = :device_id"),
            {"device_id": device_id}
        )
        dev = dev_res.mappings().first()
        if not dev or not dev["hostname"]:
            raise HTTPException(status_code=404, detail="Device tidak ditemukan atau belum pernah di-sync")

        await db.execute(
            text("DELETE FROM inventory_ip WHERE hostname = :hostname"),
            {"hostname": dev["hostname"]}
        )
        await db.commit()
        return {"ok": True, "message": f"Inventory untuk device '{dev['hostname']}' berhasil dikosongkan"}
    else:
        await db.execute(text("DELETE FROM inventory_ip"))
        await db.commit()
        return {"ok": True, "message": "Semua data inventory berhasil dikosongkan"}
