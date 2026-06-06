"""
services/sync_service.py — Orchestrator untuk sync semua / satu device F5.

Flow Sync All:
  1. Ambil semua device enabled dari DB
  2. Sync 5 device sekaligus (semaphore)
  3. Tiap device:
     a. Decrypt password
     b. F5Client: get_hostname, get_virtual_server_ips, get_pool_member_ips, get_self_ips
     c. Upsert ke inventory_ip
     d. Update last_sync, last_status di devices
  4. Return SyncResult summary

Upsert:
  INSERT ... ON CONFLICT(hostname, ip, type) DO UPDATE SET last_seen = ...
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_password
from ..models import Device, InventoryIP
from ..schemas import SyncResult, SyncDeviceResult, SyncDeviceError
from .f5_client import F5Client

logger = logging.getLogger(__name__)

SYNC_CONCURRENCY = 5  # max device yang di-sync bersamaan


async def _upsert_ip(
    db: AsyncSession,
    hostname: str,
    ip: str,
    ip_type: str,
) -> None:
    """
    Upsert satu record ke inventory_ip.
    Gunakan raw SQL untuk mendukung ON CONFLICT SQLite.
    """
    await db.execute(
        text(
            """
            INSERT INTO inventory_ip (hostname, ip, type, last_seen)
            VALUES (:hostname, :ip, :type, :now)
            ON CONFLICT(hostname, ip, type)
            DO UPDATE SET last_seen = :now
            """
        ),
        {
            "hostname": hostname,
            "ip": ip,
            "type": ip_type,
            "now": datetime.now(timezone.utc),
        },
    )


async def sync_one_device(
    db: AsyncSession,
    device: Device,
) -> SyncDeviceResult:
    """Sync satu device F5, return SyncDeviceResult."""
    result = SyncDeviceResult(
        device_id=device.id,
        name=device.name,
        management_ip=device.management_ip,
        status="FAILED",
    )

    try:
        password = decrypt_password(device.password_encrypted)
    except Exception as e:
        result.error = f"Gagal decrypt password: {e}"
        await _update_device_status(db, device.id, "FAILED", result.error)
        return result

    client_obj = F5Client(
        host=device.management_ip,
        username=device.username,
        password=password,
        verify_ssl=device.verify_ssl,
        timeout=15.0,
    )

    try:
        async with httpx.AsyncClient(
            verify=device.verify_ssl,
            timeout=15.0,
        ) as http_client:
            # 1. Ambil hostname
            hostname = await client_obj.get_hostname(http_client)
            result.hostname = hostname

            # 2. Ambil VS IPs
            vs_ips, fwd_skipped = await client_obj.get_virtual_server_ips(http_client)
            result.forwarding_vs_skipped = fwd_skipped

            # Hapus data inventory lama untuk hostname ini agar data lama/forwarding VS terhapus
            await db.execute(
                text("DELETE FROM inventory_ip WHERE hostname = :hostname"),
                {"hostname": hostname}
            )

            for ip in vs_ips:
                await _upsert_ip(db, hostname, ip, "VS")
            result.vs_ip_synced = len(vs_ips)

            # 3. Ambil Pool Member IPs
            pool_member_ips = await client_obj.get_pool_member_ips(http_client)
            for ip in pool_member_ips:
                await _upsert_ip(db, hostname, ip, "POOL_MEMBER")
            result.pool_member_ip_synced = len(pool_member_ips)
            result.node_ip_synced = len(pool_member_ips)

            # 4. Ambil Self IPs
            self_ips = await client_obj.get_self_ips(http_client)
            for ip in self_ips:
                await _upsert_ip(db, hostname, ip, "SELF_IP")
            result.self_ip_synced = len(self_ips)

        await db.commit()
        result.status = "OK"
        await _update_device_status(db, device.id, "OK", None, hostname)
        logger.info(
            f"[{device.name}] sync OK: {len(vs_ips)} VS, "
            f"{len(pool_member_ips)} POOL_MEMBER, {len(self_ips)} SELF_IP, "
            f"{fwd_skipped} forwarding skipped"
        )

    except Exception as e:
        await db.rollback()
        result.error = str(e)[:500]
        logger.warning(f"[{device.name}] sync FAILED: {e}")
        await _update_device_status(db, device.id, "FAILED", result.error)

    return result


async def _update_device_status(
    db: AsyncSession,
    device_id: int,
    status: str,
    error: str | None,
    hostname: str | None = None,
) -> None:
    try:
        await db.execute(
            text(
                """
                UPDATE devices
                SET last_sync = :now,
                    last_status = :status,
                    last_error = :error,
                    hostname = COALESCE(:hostname, hostname)
                WHERE id = :id
                """
            ),
            {
                "now": datetime.now(timezone.utc),
                "status": status,
                "error": error,
                "id": device_id,
                "hostname": hostname,
            },
        )
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to update device status for id={device_id}: {e}")


async def sync_all_devices(db: AsyncSession) -> SyncResult:
    """Sync semua device yang enabled, maksimal SYNC_CONCURRENCY bersamaan."""
    result_devices = await db.execute(
        text("SELECT * FROM devices WHERE enabled = 1")
    )
    devices = result_devices.mappings().all()

    if not devices:
        return SyncResult(
            total_devices=0,
            success=0,
            failed=0,
            vs_ip_synced=0,
            node_ip_synced=0,
            forwarding_vs_skipped=0,
            errors=[],
        )

    sem = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def guarded_sync(device_row):
        async with sem:
            # Buat session terpisah per device agar tidak conflict
            from ..database import AsyncSessionLocal
            async with AsyncSessionLocal() as dev_db:
                device = Device(
                    id=device_row["id"],
                    name=device_row["name"],
                    management_ip=device_row["management_ip"],
                    username=device_row["username"],
                    password_encrypted=device_row["password_encrypted"],
                    verify_ssl=bool(device_row["verify_ssl"]),
                    enabled=bool(device_row["enabled"]),
                )
                return await sync_one_device(dev_db, device)

    results = await asyncio.gather(
        *[guarded_sync(d) for d in devices],
        return_exceptions=False,
    )

    success = sum(1 for r in results if r.status == "OK")
    failed = len(results) - success
    total_vs = sum(r.vs_ip_synced for r in results)
    total_pool_member = sum(r.pool_member_ip_synced for r in results)
    total_self_ip = sum(r.self_ip_synced for r in results)
    total_fwd = sum(r.forwarding_vs_skipped for r in results)

    errors = [
        SyncDeviceError(
            device_id=r.device_id,
            name=r.name,
            management_ip=r.management_ip,
            status=r.status,
            error=r.error,
        )
        for r in results
        if r.status != "OK"
    ]

    return SyncResult(
        total_devices=len(results),
        success=success,
        failed=failed,
        vs_ip_synced=total_vs,
        node_ip_synced=total_pool_member,
        pool_member_ip_synced=total_pool_member,
        self_ip_synced=total_self_ip,
        forwarding_vs_skipped=total_fwd,
        errors=errors,
    )
