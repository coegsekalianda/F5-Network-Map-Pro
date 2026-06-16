"""
Sync orchestration for all devices or one F5 device.

Sync All flow:
  1. Read all enabled devices from the database.
  2. Sync up to SYNC_CONCURRENCY devices at the same time.
  3. For each device:
     a. Decrypt the password.
     b. Fetch hostname.
     c. Fetch Virtual Server, Pool Member, and Self IP records in parallel.
     d. Bulk upsert inventory and topology cache records.
     e. Update devices.last_sync and devices.last_status.
  4. Return a SyncResult summary.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_password
from ..models import Device
from ..schemas import SyncDeviceError, SyncDeviceResult, SyncResult
from .f5_client import F5Client

logger = logging.getLogger(__name__)

# SQLite WAL mode and early commits allow concurrent F5 network requests.
# Database writes are serialized using _DB_WRITE_LOCK to prevent "database is locked".
SYNC_CONCURRENCY = 5

_DB_WRITE_LOCK = asyncio.Lock()


async def _bulk_upsert_ip(db: AsyncSession, rows: list[dict]) -> None:
    if not rows:
        return

    await db.execute(
        text(
            """
            INSERT INTO inventory_ip (device_id, hostname, ip, port, type)
            VALUES (:device_id, :hostname, :ip, :port, :type)
            ON CONFLICT(hostname, ip, port, type)
            DO UPDATE SET device_id = :device_id
            """
        ),
        rows,
    )


async def _bulk_upsert_topology_vs(db: AsyncSession, rows: list[dict]) -> None:
    if not rows:
        return

    await db.execute(
        text(
            """
            INSERT INTO topology_vs_cache (
                device_id, hostname, partition, vs_name, destination,
                destination_ip, destination_port, pool_partition, pool_name, enabled
            )
            VALUES (
                :device_id, :hostname, :partition, :vs_name, :destination,
                :destination_ip, :destination_port, :pool_partition, :pool_name, :enabled
            )
            ON CONFLICT(hostname, partition, vs_name)
            DO UPDATE SET
                device_id = :device_id,
                destination = :destination,
                destination_ip = :destination_ip,
                destination_port = :destination_port,
                pool_partition = :pool_partition,
                pool_name = :pool_name,
                enabled = :enabled
            """
        ),
        rows,
    )


async def _bulk_upsert_topology_member(db: AsyncSession, rows: list[dict]) -> None:
    if not rows:
        return

    await db.execute(
        text(
            """
            INSERT INTO topology_member_cache (
                device_id, hostname, partition, pool_name, member_name, address, port
            )
            VALUES (
                :device_id, :hostname, :partition, :pool_name, :member_name, :address, :port
            )
            ON CONFLICT(hostname, partition, pool_name, member_name)
            DO UPDATE SET
                device_id = :device_id,
                address = :address,
                port = :port
            """
        ),
        rows,
    )


async def sync_one_device(
    db: AsyncSession,
    device: Device,
) -> SyncDeviceResult:
    """Sync one F5 device and return a SyncDeviceResult."""
    result = SyncDeviceResult(
        device_id=device.id,
        name=device.name,
        management_ip=device.management_ip,
        status="FAILED",
    )

    try:
        password = decrypt_password(device.password_encrypted)
    except Exception as e:
        result.error = f"Failed to decrypt password: {e}"
        async with _DB_WRITE_LOCK:
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
            hostname = await client_obj.get_hostname(http_client)
            result.hostname = hostname

            vs_task = client_obj.get_virtual_server_records(http_client)
            pool_member_task = client_obj.get_pool_member_ip_ports(http_client)
            self_ip_task = client_obj.get_self_ips(http_client)

            (vs_cache_records, fwd_skipped), pool_member_records, self_ips = await asyncio.gather(
                vs_task,
                pool_member_task,
                self_ip_task,
            )
            vs_records = [
                {"ip": item["ip"], "port": item.get("port", "")}
                for item in vs_cache_records
                if item.get("ip")
            ]
            result.forwarding_vs_skipped = fwd_skipped

        async with _DB_WRITE_LOCK:
            await db.execute(
                text(
                    """
                    DELETE FROM inventory_ip
                    WHERE device_id = :device_id
                       OR hostname = :hostname
                    """
                ),
                {"device_id": device.id, "hostname": hostname}
            )
            await db.execute(
                text("DELETE FROM topology_vs_cache WHERE device_id = :device_id OR hostname = :hostname"),
                {"device_id": device.id, "hostname": hostname},
            )
            await db.execute(
                text("DELETE FROM topology_member_cache WHERE device_id = :device_id OR hostname = :hostname"),
                {"device_id": device.id, "hostname": hostname},
            )

            inventory_rows = [
                {
                    "device_id": device.id,
                    "hostname": hostname,
                    "ip": item["ip"],
                    "port": item.get("port", "") or "",
                    "type": "VS",
                }
                for item in vs_records
            ]
            result.vs_ip_synced = len(vs_records)

            topology_vs_rows = [
                {
                    "device_id": device.id,
                    "hostname": hostname,
                    "partition": item.get("partition") or "Common",
                    "vs_name": item.get("vs_name") or "",
                    "destination": item.get("destination") or "",
                    "destination_ip": item.get("ip") or "",
                    "destination_port": item.get("port") or "",
                    "pool_partition": item.get("pool_partition") or "Common",
                    "pool_name": item.get("pool_name") or "",
                    "enabled": int(bool(item.get("enabled", True))),
                }
                for item in vs_cache_records
                if item.get("vs_name")
            ]

            pool_member_inventory_rows = []
            topology_member_rows = []
            for item in pool_member_records:
                if not item.get("ip"):
                    continue
                pool_member_inventory_rows.append({
                    "device_id": device.id,
                    "hostname": hostname,
                    "ip": item["ip"],
                    "port": item.get("port", "") or "",
                    "type": "POOL_MEMBER",
                })
                topology_member_rows.append({
                    "device_id": device.id,
                    "hostname": hostname,
                    "partition": item.get("partition") or "Common",
                    "pool_name": item.get("pool_name") or "",
                    "member_name": item.get("member_name") or f"{item.get('ip')}:{item.get('port', '')}",
                    "address": item.get("ip") or "",
                    "port": item.get("port") or "",
                })
            result.pool_member_ip_synced = len(pool_member_records)
            result.node_ip_synced = len(pool_member_records)

            self_ip_rows = [
                {
                    "device_id": device.id,
                    "hostname": hostname,
                    "ip": ip,
                    "port": "",
                    "type": "SELF_IP",
                }
                for ip in self_ips
            ]
            result.self_ip_synced = len(self_ips)

            inventory_rows.extend(pool_member_inventory_rows)
            inventory_rows.extend(self_ip_rows)

            await _bulk_upsert_ip(db, inventory_rows)
            await _bulk_upsert_topology_vs(db, topology_vs_rows)
            await _bulk_upsert_topology_member(db, topology_member_rows)

            await db.commit()

            result.status = "OK"
            await _update_device_status(db, device.id, "OK", None, hostname)

        logger.info(
            f"[{device.name}] sync OK: {len(vs_records)} VS, "
            f"{len(pool_member_records)} POOL_MEMBER, {len(self_ips)} SELF_IP, "
            f"{fwd_skipped} forwarding skipped"
        )

    except Exception as e:
        await db.rollback()
        result.error = str(e)[:500]
        logger.warning(f"[{device.name}] sync FAILED: {e}")
        async with _DB_WRITE_LOCK:
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
    """Sync all enabled devices with SYNC_CONCURRENCY parallel tasks."""
    result_devices = await db.execute(
        text("SELECT * FROM devices WHERE enabled = 1")
    )
    devices = result_devices.mappings().all()
    await db.commit()

    if not devices:
        return SyncResult(
            total_devices=0,
            success=0,
            failed=0,
            vs_ip_synced=0,
            node_ip_synced=0,
            pool_member_ip_synced=0,
            self_ip_synced=0,
            forwarding_vs_skipped=0,
            errors=[],
        )

    sem = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def guarded_sync(device_row):
        async with sem:
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
