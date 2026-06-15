import asyncio
import base64
import logging
import time
from datetime import datetime
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_password
from ..database import AsyncSessionLocal

logger = logging.getLogger(__name__)

MONITORING_TIMEOUT = 3.0


def _auth_header(username: str, password: str) -> dict:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _error_result(
    device_id,
    hostname: str,
    partition: str,
    vs_name: str,
    error: str,
    status: str = "error",
) -> dict:
    return {
        "device_id": device_id,
        "hostname": hostname,
        "partition": partition,
        "vs_name": vs_name,
        "destination": "",
        "availability_state": "unknown",
        "enabled_state": "unknown",
        "current_connections": None,
        "connection_rate": None,
        "total_connections": None,
        "timestamp": _timestamp(),
        "status": status,
        "error": error,
    }


async def _device_by_id(db: AsyncSession, device_id: int) -> Optional[dict]:
    result = await db.execute(
        text("SELECT * FROM devices WHERE id = :id"),
        {"id": device_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def _f5_object_path(partition: str, name: str) -> str:
    partition = (partition or "Common").strip() or "Common"
    name = (name or "").strip().replace("/", "~")
    return f"~{partition}~{name}"


def _f5_url(device: dict, path: str) -> str:
    return f"https://{device['management_ip']}/mgmt/tm/{path}"


async def _f5_get(device: dict, path: str, timeout: float = MONITORING_TIMEOUT) -> dict:
    password = decrypt_password(device["password_encrypted"])
    async with httpx.AsyncClient(
        verify=bool(device["verify_ssl"]),
        timeout=timeout,
    ) as client:
        response = await client.get(
            _f5_url(device, path),
            headers=_auth_header(device["username"], password),
        )
        if response.status_code == 401:
            raise PermissionError("Unauthorized")
        response.raise_for_status()
        return response.json()


def _leaf_name(key: str) -> str:
    key = str(key or "")
    path = unquote(urlparse(key).path or key).rstrip("/")
    return path.split("/")[-1]


def _flatten_stats(stats: dict) -> dict:
    flat = {}

    def walk(node: dict):
        entries = node.get("entries", {}) if isinstance(node, dict) else {}
        if not isinstance(entries, dict):
            return

        for key, value in entries.items():
            leaf = _leaf_name(key)
            if isinstance(value, dict):
                if "value" in value:
                    flat[leaf] = value.get("value")
                if "description" in value:
                    flat[leaf] = value.get("description")
                nested = value.get("nestedStats")
                if nested:
                    walk(nested)

    walk(stats)
    return flat


def _first_number(flat: dict, names: tuple[str, ...]) -> Optional[int]:
    lookup = {str(k).lower(): v for k, v in flat.items()}
    for name in names:
        value = lookup.get(name.lower())
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _first_text(flat: dict, names: tuple[str, ...], default: str = "") -> str:
    lookup = {str(k).lower(): v for k, v in flat.items()}
    for name in names:
        value = lookup.get(name.lower())
        if value not in (None, ""):
            return str(value)
    return default


def normalize_f5_vs_stats(raw_stats: dict) -> dict:
    flat = _flatten_stats(raw_stats)
    return {
        "destination": _first_text(flat, ("destination", "addr", "address"), ""),
        "availability_state": _first_text(
            flat,
            ("status.availabilityState", "availabilityState"),
            "unknown",
        ),
        "enabled_state": _first_text(
            flat,
            ("status.enabledState", "enabledState"),
            "unknown",
        ),
        "current_connections": _first_number(
            flat,
            (
                "clientside.curConns",
                "curConns",
                "currentConnections",
                "serverside.curConns",
            ),
        ),
        "connection_rate": _first_number(
            flat,
            (
                "clientside.connRate",
                "connRate",
                "connectionsPerSecond",
                "connectionRate",
            ),
        ),
        "total_connections": _first_number(
            flat,
            (
                "clientside.totConns",
                "totConns",
                "totalConns",
                "totalConnections",
            ),
        ),
    }


async def list_virtual_servers_for_device(db: AsyncSession, device_id: int) -> dict:
    device = await _device_by_id(db, device_id)
    if not device:
        return {"device_id": device_id, "items": [], "status": "error", "error": "Device not found"}

    # Commit early to release database transaction before slow REST calls
    await db.commit()

    try:
        data = await _f5_get(
            device,
            "ltm/virtual?$select=name,partition,destination,enabled&$top=5000",
            timeout=10.0,
        )
        items = []
        for item in data.get("items", []):
            items.append({
                "name": item.get("name", ""),
                "partition": item.get("partition", "Common"),
                "destination": item.get("destination", ""),
                "enabled": bool(item.get("enabled", False)),
            })
        items.sort(key=lambda row: (row["partition"], row["name"]))
        return {
            "device_id": device_id,
            "hostname": device.get("hostname") or device.get("name") or device.get("management_ip"),
            "items": items,
            "status": "ok",
            "error": None,
        }
    except Exception as e:
        return {
            "device_id": device_id,
            "hostname": device.get("hostname") or device.get("name") or device.get("management_ip"),
            "items": [],
            "status": "error",
            "error": str(e)[:300],
        }


async def get_vs_connection_stats(
    db: AsyncSession,
    device_id: int,
    partition: str,
    vs_name: str,
) -> dict:
    device = await _device_by_id(db, device_id)
    if not device:
        return _error_result(device_id, "", partition, vs_name, "Device not found")

    # Commit early to release database transaction before slow REST calls
    await db.commit()

    hostname = device.get("hostname") or device.get("name") or device.get("management_ip")
    path = f"ltm/virtual/{_f5_object_path(partition, vs_name)}/stats"
    started = time.perf_counter()

    try:
        raw_stats = await asyncio.wait_for(
            _f5_get(device, path, timeout=MONITORING_TIMEOUT),
            timeout=MONITORING_TIMEOUT + 0.5,
        )
        normalized = normalize_f5_vs_stats(raw_stats)
        duration = round(time.perf_counter() - started, 3)
        logger.info(
            "monitoring stats ok device=%s vs=%s duration=%ss",
            hostname,
            vs_name,
            duration,
        )
        return {
            "device_id": device_id,
            "hostname": hostname,
            "partition": partition or "Common",
            "vs_name": vs_name,
            "destination": normalized["destination"],
            "availability_state": normalized["availability_state"],
            "enabled_state": normalized["enabled_state"],
            "current_connections": normalized["current_connections"],
            "connection_rate": normalized["connection_rate"],
            "total_connections": normalized["total_connections"],
            "timestamp": _timestamp(),
            "status": "ok",
            "error": None,
        }
    except (httpx.TimeoutException, asyncio.TimeoutError):
        duration = round(time.perf_counter() - started, 3)
        logger.warning(
            "monitoring stats timeout device=%s vs=%s duration=%ss",
            hostname,
            vs_name,
            duration,
        )
        return _error_result(device_id, hostname, partition, vs_name, "Timeout connecting to device", "timeout")
    except Exception as e:
        duration = round(time.perf_counter() - started, 3)
        logger.warning(
            "monitoring stats error device=%s vs=%s duration=%ss error=%s",
            hostname,
            vs_name,
            duration,
            e,
        )
        return _error_result(device_id, hostname, partition, vs_name, str(e)[:300])


async def get_batch_vs_connection_stats(db: AsyncSession, targets: list[dict]) -> list[dict]:
    async def run_target(target: dict) -> dict:
        async with AsyncSessionLocal() as target_db:
            return await get_vs_connection_stats(
                target_db,
                int(target.get("device_id")),
                target.get("partition") or "Common",
                target.get("vs_name") or "",
            )

    tasks = [run_target(target) for target in targets if target.get("device_id") and target.get("vs_name")]
    if not tasks:
        return []
    return await asyncio.gather(*tasks, return_exceptions=False)
