import sys
import os

# ─── sys.path injection ────────────────────────────────────────────────────
# Ensure the project root (parent of backend/) is on sys.path so that
# `from backend.xxx import ...` works regardless of how uvicorn is invoked.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# ────────────────────────────────────────────────────────────────────────────

import httpx
import asyncio
import re as _re
import time
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import base64
import shlex
from typing import Optional
from urllib.parse import quote, unquote, urlparse
from sqlalchemy import select, text
from backend.database import AsyncSessionLocal
from backend.models import Device
from backend.services.sync_service import sync_one_device

app = FastAPI(title="F5 Network Map Pro", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "..", "frontend", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ─── Inventory Feature: routers ───────────────────────────────────────────────
from backend.routers import devices as devices_router
from backend.routers import inventory as inventory_router
from backend.routers import monitoring as monitoring_router
from backend.routers import sync as sync_router

app.include_router(devices_router.router)
app.include_router(inventory_router.router)
app.include_router(monitoring_router.router)
app.include_router(sync_router.router)


@app.on_event("startup")
async def startup_event():
    from backend.database import init_db
    await init_db()

# ──────────────────────────────────────────────────────────────────────────────


class F5Config(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False


class MemberActionRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    partition: str
    pool_name: str
    member_name: str
    action: str

class BulkMemberActionRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    members: list
    action: str

class VSActionRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    partition: str
    vs_name: str
    action: str 

class ClearPoolConnectionsRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    partition: str
    pool_name: str


class VSExtraRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    partition: str
    vs_name: str


class IRuleContentRequest(BaseModel):
    host: str
    username: str
    password: str
    verify_ssl: bool = False
    partition: str = "Common"
    rule_name: str


def get_headers(cfg: F5Config) -> dict:
    creds = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


F5_SEMAPHORE = asyncio.Semaphore(15)
_AUTO_SYNC_LOCK = asyncio.Lock()
_AUTO_SYNC_IN_PROGRESS: set[str] = set()

async def f5_get(cfg: F5Config, path: str, client: httpx.AsyncClient = None) -> dict:
    async with F5_SEMAPHORE:
        url = f"https://{cfg.host}/mgmt/tm/{path}"
        if client:
            r = await client.get(url, headers=get_headers(cfg))
            if r.status_code == 401:
                raise HTTPException(status_code=401, detail="Unauthorized — cek username/password")
            if r.status_code == 404:
                return {"items": []}
            r.raise_for_status()
            return r.json()
        else:
            async with httpx.AsyncClient(verify=cfg.verify_ssl, timeout=20.0) as local_client:
                r = await local_client.get(url, headers=get_headers(cfg))
                if r.status_code == 401:
                    raise HTTPException(status_code=401, detail="Unauthorized — cek username/password")
                if r.status_code == 404:
                    return {"items": []}
                r.raise_for_status()
                return r.json()


async def f5_get_collection_items(
    cfg: F5Config,
    collection_path: str,
    select: str,
    page_size: int = 5000,
) -> list[dict]:
    items: list[dict] = []
    skip = 0
    seen_pages: set[tuple] = set()

    while True:
        data = await f5_get(
            cfg,
            f"{collection_path}?$select={select}&$top={page_size}&$skip={skip}",
        )
        page_items = data.get("items", []) if isinstance(data, dict) else []
        if not page_items:
            break

        signature = tuple(
            item.get("selfLink") or item.get("fullPath") or item.get("name")
            for item in page_items[:5]
        )
        if signature in seen_pages:
            break
        seen_pages.add(signature)

        items.extend(page_items)

        total_items = data.get("totalItems")
        if isinstance(total_items, int) and len(items) >= total_items:
            break
        if len(page_items) < page_size:
            break

        skip += page_size

    return items


def _looks_like_ip(s: str) -> bool:
    return bool(
        _re.match(r'^\d{1,3}(\.\d{0,3}){0,3}(:\d*)?$', s)
        or _re.match(r'^\d{1,3}(\.\d{1,3}){3}\.\d+$', s)
    )


def _is_full_ip(s: str) -> bool:
    return bool(_re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', s))


def _parse_ip_port(q: str):
    """Split '10.1.2.3:80' or '10.1.2.3.80' into ('10.1.2.3', '80')."""
    if ':' in q:
        parts = q.rsplit(':', 1)
        return parts[0], parts[1]
    m = _re.match(r'^(\d{1,3}(?:\.\d{1,3}){3})\.(\d+)$', q)
    if m:
        return m.group(1), m.group(2)
    return q, None


def _clean_f5_address(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("/"):
        value = value.split("/")[-1]
    return value


def _ip_matches(query_ip: str, actual_ip: str, suffix_only: bool = False) -> bool:
    actual_ip = (actual_ip or "").split("%", 1)[0]
    if suffix_only:
        return actual_ip == query_ip or actual_ip.endswith(f".{query_ip}")
    return query_ip in actual_ip


def _address_matches(query_ip: str, member_address: str, member_name: str, suffix_only: bool = False) -> bool:
    member_address = _clean_f5_address(member_address)
    member_name = _clean_f5_address(member_name)

    candidates = []
    if member_address:
        candidates.append(member_address)
    if ":" in member_name:
        candidates.append(member_name.rsplit(":", 1)[0])

    for candidate in candidates:
        if _ip_matches(query_ip, candidate, suffix_only=suffix_only):
            return True

    return False


def _member_port(member: dict) -> str:
    port = member.get("port")
    if port not in (None, ""):
        return str(port)

    name = member.get("name", "")
    if ":" in name:
        return name.rsplit(":", 1)[-1]

    return ""


def _port_matches(query_port: str, actual_port: str) -> bool:
    if not query_port:
        return True
    return str(actual_port or "").startswith(str(query_port))


def _tmsh_member_state(body: str) -> str:
    body = (body or "").lower()
    session = ""
    state = ""

    session_match = _re.search(r'\bsession\s+(\S+)', body)
    if session_match:
        session = session_match.group(1)

    state_match = _re.search(r'\bstate\s+(\S+)', body)
    if state_match:
        state = state_match.group(1)

    if session == "user-disabled":
        return "force-offline"

    if state in ("up", "checking", "unchecked", "available", "fqdn-up", "enabled"):
        return "up"

    return "down"


def _pool_ref_parts(pool_ref: str, default_partition: str = "Common"):
    if not pool_ref:
        return default_partition, ""

    if pool_ref.startswith("/"):
        parts = pool_ref.strip("/").split("/")
        partition = parts[0] if len(parts) > 1 else default_partition
        pool_name = parts[-1]
    else:
        partition = default_partition
        pool_name = pool_ref.split("/")[-1]

    return partition, pool_name

async def fetch_node_map(cfg: F5Config, client: httpx.AsyncClient = None):
    try:
        data = await f5_get(
            cfg,
            "ltm/node?$select=name,address,session,state",
            client=client
        )

        node_map = {}

        for n in data.get("items", []):
            addr = n.get("address", "")

            if addr.startswith("/"):
                addr = addr.split("/")[-1]

            state = n.get("state", "").lower()
            session = n.get("session", "").lower()

            if session == "user-disabled":
                node_map[addr] = "disabled"
            elif state in ("up", "unchecked"):
                node_map[addr] = "up"
            else:
                node_map[addr] = "down"

        return node_map

    except Exception:
        return {}


CONNECTION_STAT_NAMES = ("serverside.curConns", "curConns", "statistics.curConns")


def _stat_key_matches(key: str, names: tuple[str, ...]) -> bool:
    key = str(key or "")
    if key in names:
        return True

    path = unquote(urlparse(key).path or key).rstrip("/")
    leaf = path.split("/")[-1]
    return leaf in names


def _stat_number(stats_data: dict, names: tuple[str, ...]) -> int:
    if not isinstance(stats_data, dict):
        return 0

    entries = stats_data.get("entries", {})
    if not isinstance(entries, dict):
        return 0

    total = 0
    for key, value in entries.items():
        if _stat_key_matches(key, names) and isinstance(value, dict):
            try:
                total += int(value.get("value", 0))
            except (TypeError, ValueError):
                pass

        if isinstance(value, dict):
            nested = value.get("nestedStats")
            if nested:
                total += _stat_number(nested, names)

    return total


def _profile_type_from_reference(link: str) -> Optional[str]:
    if not link:
        return None

    path = unquote(urlparse(link).path).lower()
    marker = "/mgmt/tm/ltm/profile/"
    if marker not in path:
        return None

    tail = path.split(marker, 1)[1].strip("/")
    profile_type = tail.split("/", 1)[0].strip()
    return profile_type or None


def _profile_type_from_kind(kind: str) -> Optional[str]:
    if not kind:
        return None

    parts = kind.lower().split(":")
    try:
        profile_idx = parts.index("profile")
        profile_type = parts[profile_idx + 1]
        return profile_type or None
    except (ValueError, IndexError):
        return None


def _profile_partition(profile_item: dict, default_partition: str) -> str:
    full_path = profile_item.get("fullPath", "")
    if isinstance(full_path, str) and full_path.startswith("/"):
        parts = full_path.strip("/").split("/")
        if len(parts) > 1:
            return parts[0]

    return profile_item.get("partition") or default_partition


def _profile_leaf_name(profile_item: dict) -> str:
    name = profile_item.get("name")
    if name:
        return name

    full_path = profile_item.get("fullPath", "")
    if isinstance(full_path, str):
        return full_path.strip("/").split("/")[-1]

    return ""


PROFILE_TYPE_ENDPOINTS = (
    "analytics",
    "client-ssl",
    "server-ssl",
    "http",
    "http2",
    "tcp",
    "udp",
    "one-connect",
    "fastl4",
    "rewrite",
    "web-acceleration",
    "http-compression",
    "sctp",
    "stream",
    "ftp",
    "dns",
    "diameter",
    "websocket",
    "request-log",
)


class ProfileTypeResolver:
    def __init__(self, cfg: F5Config, client: httpx.AsyncClient = None):
        self.cfg = cfg
        self.client = client
        self._by_partition = {}
        self._by_name = {}
        self._load_task = None
        self._tls_tasks = {}

    async def resolve(self, profile_item: dict, default_partition: str) -> str:
        link = profile_item.get("nameReference", {}).get("link")
        profile_type = _profile_type_from_reference(link)
        if profile_type:
            return profile_type

        profile_type = _profile_type_from_kind(profile_item.get("kind", ""))
        if profile_type:
            return profile_type

        profile_name = _profile_leaf_name(profile_item)
        if not profile_name:
            return "unknown"

        profile_partition = _profile_partition(profile_item, default_partition)
        await self._ensure_loaded()

        exact_key = (profile_partition.lower(), profile_name.lower())
        profile_type = self._by_partition.get(exact_key)
        if profile_type:
            return profile_type

        candidates = self._by_name.get(profile_name.lower(), set())
        if len(candidates) == 1:
            return next(iter(candidates))

        return "unknown"

    async def _ensure_loaded(self):
        if self._load_task is None:
            self._load_task = asyncio.create_task(self._load_profiles())
        await self._load_task

    async def _load_profiles(self):
        tasks = [
            f5_get(
                self.cfg,
                f"ltm/profile/{endpoint}?$select=name,partition,fullPath&$top=5000",
                client=self.client,
            )
            for endpoint in PROFILE_TYPE_ENDPOINTS
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for endpoint, data in zip(PROFILE_TYPE_ENDPOINTS, results):
            if isinstance(data, Exception):
                logging.debug(f"Profile type collection lookup failed for {endpoint}: {data}")
                continue

            for item in data.get("items", []):
                name = _profile_leaf_name(item)
                partition = _profile_partition(item, item.get("partition", "Common"))
                if not name:
                    continue

                self._by_partition[(partition.lower(), name.lower())] = endpoint
                self._by_name.setdefault(name.lower(), set()).add(endpoint)

    async def tls_versions(self, partition: str, profile_name: str) -> list:
        key = (partition, profile_name)
        if key not in self._tls_tasks:
            self._tls_tasks[key] = asyncio.create_task(
                self._fetch_tls_versions(partition, profile_name)
            )

        return await self._tls_tasks[key]

    async def _fetch_tls_versions(self, partition: str, profile_name: str) -> list:
        p_name_encoded = profile_name.replace("/", "~")
        tls_path = f"ltm/profile/client-ssl/~{partition}~{p_name_encoded}"
        tls_data = await f5_get(self.cfg, tls_path, client=self.client)
        return parse_tls_versions(tls_data)

async def fetch_pool_detail(cfg: F5Config, pool_name: str, partition: str, node_map: dict = None, client: httpx.AsyncClient = None) -> dict:
    pool_data, members_data, stats_data, member_stats_data = await asyncio.gather(
        f5_get(cfg, f"ltm/pool/~{partition}~{pool_name}", client=client),
        f5_get(
            cfg,
            f"ltm/pool/~{partition}~{pool_name}/members?$select=name,address,session,state,port,ratio",
            client=client
        ),
        f5_get(cfg, f"ltm/pool/~{partition}~{pool_name}/stats", client=client),
        f5_get(cfg, f"ltm/pool/~{partition}~{pool_name}/members/stats", client=client),
        return_exceptions=True
    )

    if isinstance(pool_data, Exception):
        print(f"Error fetching pool detail for {pool_name}: {pool_data}")
    if isinstance(members_data, Exception):
        print(f"Error fetching pool members for {pool_name}: {members_data}")
    if isinstance(stats_data, Exception):
        print(f"Error fetching pool stats for {pool_name}: {stats_data}")
    if isinstance(member_stats_data, Exception):
        logging.debug(f"Error fetching pool member stats for {pool_name}: {member_stats_data}")

    members = []

    up_states = (
        "up",
        "checking",
        "unchecked",
        "available",
        "fqdn-up",
        "enabled"
    )

    if isinstance(members_data, dict):
        for m in members_data.get("items", []):

            m_state = m.get("state", "").lower()
            m_session = m.get("session", "").lower()

            m_name = m.get("name", "")
            m_address = m.get("address", "")

            if m_address.startswith("/"):
                m_address = m_address.split("/")[-1]

            display_address = m_address

            is_fqdn_member = m_address in ("any", "any6", "")

            if is_fqdn_member:
                if ":" in m_name:
                    display_address = m_name.rsplit(":", 1)[0]
                else:
                    display_address = m_name

                n_state = "unknown"

            else:
                n_state = (node_map or {}).get(m_address, "unknown")

            if n_state == "down" and m_state in up_states:
                n_state = "up"

            if m_session == "user-disabled":
                state = "force-offline"

            elif not is_fqdn_member and n_state == "down":
                state = "node-disabled"

            elif not is_fqdn_member and n_state == "disabled":
                state = "node-disabled"

            elif m_state in up_states:
                state = "up"

            else:
                state = "down"

            port = m.get("port")

            if not port:
                parts = m_name.split(":")
                port = parts[-1] if len(parts) > 1 else "—"

            members.append({
                "name": m_name,
                "address": display_address,
                "port": str(port),
                "state": state,
                "session": m_session,
                "nodeState": n_state,
            })

    active = [
        m for m in members
        if m["state"] == "up"
    ]

    pool_status = "up" if active else "down"

    monitor = ""
    lbMode = "round-robin"
    snat = ""
    current_connections = 0

    if isinstance(pool_data, dict):
        monitor = pool_data.get("monitor", "")
        lbMode = pool_data.get("loadBalancingMode", "round-robin")
        snat = pool_data.get("snat", "")

    if isinstance(stats_data, dict):
        current_connections = _stat_number(
            stats_data,
            CONNECTION_STAT_NAMES,
        )
    if isinstance(member_stats_data, dict):
        current_connections = max(
            current_connections,
            _stat_number(member_stats_data, CONNECTION_STAT_NAMES),
        )

    return {
        "name": pool_name,
        "partition": partition,
        "status": pool_status,
        "members": members,
        "monitor": monitor,
        "lbMode": lbMode,
        "snat": snat,
        "current_connections": current_connections,
    }


def build_pool_detail_from_data(
    pool_name: str,
    partition: str,
    pool_data: dict,
    member_items: list,
    node_map: dict = None,
    stats_data: dict = None,
) -> dict:
    members = []
    up_states = ("up", "checking", "unchecked", "available", "fqdn-up", "enabled")

    for m in member_items:
        m_state = m.get("state", "").lower()
        m_session = m.get("session", "").lower()
        m_name = m.get("name", "")
        m_address = m.get("address", "")

        if m_address.startswith("/"):
            m_address = m_address.split("/")[-1]

        display_address = m_address
        is_fqdn_member = m_address in ("any", "any6", "")

        if is_fqdn_member:
            display_address = m_name.rsplit(":", 1)[0] if ":" in m_name else m_name
            n_state = "unknown"
        else:
            n_state = (node_map or {}).get(m_address, "unknown")

        if n_state == "down" and m_state in up_states:
            n_state = "up"

        if m_session == "user-disabled":
            state = "force-offline"
        elif not is_fqdn_member and n_state in ("down", "disabled"):
            state = "node-disabled"
        elif m_state in up_states:
            state = "up"
        else:
            state = "down"

        port = m.get("port")
        if not port:
            parts = m_name.split(":")
            port = parts[-1] if len(parts) > 1 else "-"

        members.append({
            "name": m_name,
            "address": display_address,
            "port": str(port),
            "state": state,
            "session": m_session,
            "nodeState": n_state,
        })

    active = [m for m in members if m["state"] == "up"]
    current_connections = 0

    if isinstance(stats_data, dict):
        current_connections = _stat_number(
            stats_data,
            CONNECTION_STAT_NAMES,
        )

    return {
        "name": pool_name,
        "partition": partition,
        "status": "up" if active else "down",
        "members": members,
        "monitor": pool_data.get("monitor", "") if isinstance(pool_data, dict) else "",
        "lbMode": pool_data.get("loadBalancingMode", "round-robin") if isinstance(pool_data, dict) else "round-robin",
        "snat": pool_data.get("snat", "") if isinstance(pool_data, dict) else "",
        "current_connections": current_connections,
    }


def _pool_stats_by_key(stats_data: dict) -> dict:
    if not isinstance(stats_data, dict):
        return {}

    result = {}
    for key, value in stats_data.get("entries", {}).items():
        path = unquote(urlparse(key).path)
        match = _re.search(r'/ltm/pool/~([^~]+)~([^/]+)/stats$', path)
        if not match:
            continue

        partition = match.group(1)
        pool_name = match.group(2)
        nested = value.get("nestedStats", {}) if isinstance(value, dict) else {}
        result[f"{partition}/{pool_name}"] = nested

    return result


async def fetch_pool_details_bulk(
    cfg: F5Config,
    pool_keys: set,
    node_map: dict = None,
    client: httpx.AsyncClient = None,
    fallback_missing: bool = True,
) -> dict:
    if not pool_keys:
        return {}

    if fallback_missing and len(pool_keys) <= 50:
        sem = asyncio.Semaphore(20)

        async def fetch_one(key: str):
            partition, pool_name = key.split("/", 1)
            async with sem:
                return key, await fetch_pool_detail(cfg, pool_name, partition, node_map, client=client)

        results = await asyncio.gather(
            *[fetch_one(key) for key in pool_keys],
            return_exceptions=True,
        )
        details = {}
        for item in results:
            if isinstance(item, Exception):
                logging.debug(f"Pool detail fetch failed: {item}")
                continue
            key, detail = item
            details[key] = detail
        return details

    pool_data, stats_data = await asyncio.gather(
        f5_get(
            cfg,
            "ltm/pool?expandSubcollections=true&$select=name,partition,monitor,loadBalancingMode,snat,membersReference&$top=5000",
            client=client,
        ),
        f5_get(cfg, "ltm/pool/stats", client=client),
        return_exceptions=True,
    )

    details = {}
    stats_by_key = _pool_stats_by_key(stats_data) if isinstance(stats_data, dict) else {}

    if isinstance(pool_data, dict):
        for pool in pool_data.get("items", []):
            pool_name = pool.get("name", "")
            partition = pool.get("partition", "Common")
            key = f"{partition}/{pool_name}"
            if key not in pool_keys:
                continue

            member_items = pool.get("membersReference", {}).get("items", [])
            details[key] = build_pool_detail_from_data(
                pool_name,
                partition,
                pool,
                member_items,
                node_map=node_map,
                stats_data=stats_by_key.get(key, {}),
            )

    missing_keys = pool_keys - set(details.keys())
    if not missing_keys or not fallback_missing:
        return details

    sem = asyncio.Semaphore(20)

    async def fetch_missing(key: str):
        partition, pool_name = key.split("/", 1)
        async with sem:
            return key, await fetch_pool_detail(cfg, pool_name, partition, node_map, client=client)

    fallback_results = await asyncio.gather(
        *[fetch_missing(key) for key in missing_keys],
        return_exceptions=True,
    )

    for item in fallback_results:
        if isinstance(item, Exception):
            logging.debug(f"Bulk pool detail fallback failed: {item}")
            continue

        key, detail = item
        details[key] = detail

    return details


def parse_tls_versions(profile_data: dict) -> list:
    # Default set of TLS versions supported on BIG-IP
    versions = ["TLS 1.0", "TLS 1.1", "TLS 1.2", "TLS 1.3"]
    
    if not isinstance(profile_data, dict):
        return versions

    options_val = profile_data.get("tmOptions") or profile_data.get("options")
    if not options_val:
        return versions

    options_str = ""
    if isinstance(options_val, list):
        options_str = " ".join(options_val).lower()
    elif isinstance(options_val, str):
        options_str = options_val.lower()

    if "no-tlsv1.3" in options_str:
        if "TLS 1.3" in versions:
            versions.remove("TLS 1.3")
    if "no-tlsv1.2" in options_str:
        if "TLS 1.2" in versions:
            versions.remove("TLS 1.2")
    if "no-tlsv1.1" in options_str:
        if "TLS 1.1" in versions:
            versions.remove("TLS 1.1")

    # Check "no-tlsv1" safely
    cleaned = options_str.replace("no-tlsv1.3", "").replace("no-tlsv1.2", "").replace("no-tlsv1.1", "")
    if "no-tlsv1" in cleaned:
        if "TLS 1.0" in versions:
            versions.remove("TLS 1.0")

    return versions


async def _extract_vs_extra(
    vs_data: dict,
    partition: str,
    profile_resolver: ProfileTypeResolver = None,
    include_tls: bool = True,
    include_profiles: bool = True,
):
    rules = []
    for r in vs_data.get("rules", []):
        if r.lower() not in ("none",):
            rules.append(r.split("/")[-1])

    profiles = []
    if not include_profiles:
        return rules, profiles

    prof_ref = vs_data.get("profilesReference", {})
    for p in prof_ref.get("items", []):
        p_name = _profile_leaf_name(p)
        p_partition = _profile_partition(p, partition)
        if profile_resolver:
            p_type = await profile_resolver.resolve(p, partition)
        else:
            p_type = (
                _profile_type_from_reference(p.get("nameReference", {}).get("link"))
                or _profile_type_from_kind(p.get("kind", ""))
                or "unknown"
            )

        profile_dict = {"type": p_type, "name": p_name}

        if include_tls and p_type == "client-ssl":
            try:
                if profile_resolver:
                    profile_dict["tls_versions"] = await profile_resolver.tls_versions(p_partition, p_name)
                else:
                    profile_dict["tls_versions"] = []
            except Exception as e_tls:
                logging.debug(f"Failed to fetch TLS versions for {p_name} in {partition}: {e_tls}")
                profile_dict["tls_versions"] = []
        profiles.append(profile_dict)

    return rules, profiles


async def fetch_vs_extra_rest(
    cfg: F5Config,
    partition: str,
    vs_name: str,
    client: httpx.AsyncClient = None,
    profile_resolver: ProfileTypeResolver = None,
    include_tls: bool = True,
    include_profiles: bool = True,
):
    """
    Fetch iRules and profiles through REST API (expandSubcollections=true)
    without using tmsh or bash. Retry up to 2 times on transient failures.
    """
    vs_name_encoded = vs_name.replace("/", "~")
    path = f"ltm/virtual/~{partition}~{vs_name_encoded}?expandSubcollections=true"

    last_err = None
    for attempt in range(3):
        try:
            data = await f5_get(cfg, path, client=client)
            return await _extract_vs_extra(
                data,
                partition,
                profile_resolver,
                include_tls=include_tls,
                include_profiles=include_profiles,
            )
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))

    logging.warning(f"Error fetching VS extra for {vs_name} after 3 attempts: {type(last_err).__name__}: {last_err}")
    return [], []


async def fetch_vs_extras_bulk(
    cfg: F5Config,
    vs_keys: set,
    client: httpx.AsyncClient = None,
    profile_resolver: ProfileTypeResolver = None,
    include_tls: bool = True,
    include_profiles: bool = True,
) -> dict:
    if not vs_keys:
        return {}

    try:
        data = await f5_get(
            cfg,
            "ltm/virtual?expandSubcollections=true&$top=5000",
            client=client,
        )
    except Exception as e:
        logging.debug(f"Bulk VS extra lookup failed: {e}")
        return {}

    extras = {}
    for vs in data.get("items", []):
        partition = vs.get("partition", "Common")
        name = vs.get("name", "")
        key = f"{partition}/{name}"
        if key not in vs_keys:
            continue

        try:
            extras[key] = await _extract_vs_extra(
                vs,
                partition,
                profile_resolver,
                include_tls=include_tls,
                include_profiles=include_profiles,
            )
        except Exception as e:
            logging.debug(f"Bulk VS extra parse failed for {key}: {e}")
            extras[key] = ([], [])

        if len(extras) >= len(vs_keys):
            break

    return extras


async def fetch_vs_extras_targeted(
    cfg: F5Config,
    vs_keys: set,
    client: httpx.AsyncClient = None,
    profile_resolver: ProfileTypeResolver = None,
    include_tls: bool = True,
    include_profiles: bool = True,
) -> dict:
    if not vs_keys:
        return {}

    sem = asyncio.Semaphore(12)

    async def fetch_one(key: str):
        partition, vs_name = key.split("/", 1)
        async with sem:
            return key, await fetch_vs_extra_rest(
                cfg,
                partition,
                vs_name,
                client=client,
                profile_resolver=profile_resolver,
                include_tls=include_tls,
                include_profiles=include_profiles,
            )

    results = await asyncio.gather(
        *[fetch_one(key) for key in vs_keys],
        return_exceptions=True,
    )

    extras = {}
    for item in results:
        if isinstance(item, Exception):
            logging.debug(f"Targeted VS extra lookup failed: {item}")
            continue
        key, detail = item
        extras[key] = detail
    return extras


async def build_vs_result(
    cfg: F5Config,
    vs: dict,
    node_map: dict = None,
    client: httpx.AsyncClient = None,
    profile_resolver: ProfileTypeResolver = None,
    pool_detail_map: dict = None,
    vs_extra_map: dict = None,
    skip_vs_extra: bool = False,
) -> dict:
    partition = vs.get("partition", "Common")
    vs_name = vs.get("name")
    pool_partition, pool_name = _pool_ref_parts(vs.get("pool", ""), partition) if vs.get("pool") else (partition, None)
    vs_key = f"{partition}/{vs_name}"

    extra_result = None
    extra_task = None
    if vs_extra_map is not None and vs_key in vs_extra_map:
        extra_result = vs_extra_map[vs_key]
    elif skip_vs_extra:
        extra_result = ([], [])
    else:
        extra_task = fetch_vs_extra_rest(
            cfg,
            partition,
            vs_name,
            client=client,
            profile_resolver=profile_resolver,
            include_tls=False,
            include_profiles=False,
        )

    if pool_name:
        pool_key = f"{pool_partition}/{pool_name}"
        if pool_detail_map is not None:
            if extra_task:
                extra_result = await extra_task
            pool_result = pool_detail_map.get(pool_key)
        else:
            pool_task = fetch_pool_detail(cfg, pool_name, pool_partition, node_map, client=client)
            if extra_task:
                extra_result, pool_result = await asyncio.gather(
                    extra_task,
                    pool_task,
                    return_exceptions=True
                )
            else:
                pool_result = await pool_task
    else:
        if extra_task:
            extra_result = await extra_task
        pool_result = None

    if isinstance(extra_result, Exception):
        rules, profiles = [], []
    else:
        rules, profiles = extra_result
    if not rules and isinstance(vs.get("rules"), list):
        rules = [
            str(rule).split("/")[-1]
            for rule in vs.get("rules", [])
            if str(rule).lower() not in ("none", "")
        ]

    # Extract SNAT info from VS
    sat = vs.get("sourceAddressTranslation", {})
    if isinstance(sat, dict):
        sat_type = sat.get("type", "")
        sat_pool = sat.get("pool", "")
        if sat_type == "snat" and sat_pool:
            snat_label = f"SNAT Pool: {sat_pool.split('/')[-1]}"
        elif sat_type == "automap":
            snat_label = "Automap"
        elif sat_type == "none":
            snat_label = "None"
        else:
            snat_label = sat_type or ""
    else:
        snat_label = ""

    vs_obj = {
        "name": vs_name,
        "destination": vs.get("destination", ""),
        "partition": partition,
        "enabled": vs.get("enabled", False),
        "status": "up" if vs.get("enabled", False) else "down",
        "protocol": vs.get("ipProtocol", "tcp"),
        "description": vs.get("description", ""),
        "pool": pool_name,
        "rules": rules,
        "profiles": profiles,
        "pools": [],
        "snat": snat_label,
    }

    if pool_name and isinstance(pool_result, dict):
        vs_obj["pools"].append(pool_result)

    return vs_obj


# Absolute path to index.html — works regardless of uvicorn CWD
_INDEX_HTML = os.path.join(BASE_DIR, "..", "frontend", "index.html")
_INDEX_HTML = os.path.normpath(_INDEX_HTML)


@app.get("/")
async def root():
    return FileResponse(_INDEX_HTML)


@app.get("/monitoring")
async def monitoring_page():
    return FileResponse(_INDEX_HTML)

async def f5_bash(cfg: F5Config, cmd: str, client: httpx.AsyncClient = None) -> str:
    async with F5_SEMAPHORE:
        url = f"https://{cfg.host}/mgmt/tm/util/bash"

        payload = {
            "command": "run",
            "utilCmdArgs": f"-c {shlex.quote(cmd)}"
        }

        if client:
            r = await client.post(url, headers=get_headers(cfg), json=payload)
            if r.status_code == 401:
                raise HTTPException(status_code=401, detail="Unauthorized — cek username/password")
            r.raise_for_status()
            return r.json().get("commandResult", "")
        else:
            async with httpx.AsyncClient(verify=cfg.verify_ssl, timeout=30.0) as local_client:
                r = await local_client.post(url, headers=get_headers(cfg), json=payload)
                if r.status_code == 401:
                    raise HTTPException(status_code=401, detail="Unauthorized — cek username/password")
                r.raise_for_status()
                return r.json().get("commandResult", "")

def _pool_member_targets(members_data: dict) -> tuple[list[tuple[str, str]], list[str]]:
    targets = []
    seen = set()
    skipped = []

    for member in members_data.get("items", []):
        member_name = member.get("name", "")
        address = member.get("address", "")
        port = member.get("port")

        if address.startswith("/"):
            address = address.split("/")[-1]
        if not port and ":" in member_name:
            port = member_name.rsplit(":", 1)[-1]
        if address in ("", "any", "any6") or not port:
            skipped.append(member_name or address or "unknown")
            continue

        key = (address, str(port))
        if key not in seen:
            seen.add(key)
            targets.append(key)

    return targets, skipped


def _parse_tmsh_connection_count(output: str, address: str = "", port: str = "") -> int:
    total_match = _re.search(r"(?i)total\s+records\s+returned\s*:\s*(\d+)", output or "")
    if total_match:
        return int(total_match.group(1))

    endpoint = f"{address}:{port}" if address and port else ""
    count = 0
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("sys::", "total records")):
            continue
        if endpoint and endpoint in line:
            count += 1
        elif _re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:%\d+)?:\d+\b", line):
            count += 1

    return count


async def _count_member_connections(
    cfg: F5Config,
    targets: list[tuple[str, str]],
    client: httpx.AsyncClient = None,
) -> tuple[int, list[dict]]:
    if not targets:
        return 0, []

    sem = asyncio.Semaphore(8)

    async def count_target(address: str, port: str):
        cmd = (
            "tmsh -q show sys connection "
            f"ss-server-addr {shlex.quote(address)} "
            f"ss-server-port {shlex.quote(str(port))}"
        )
        async with sem:
            try:
                output = await f5_bash(cfg, cmd, client=client)
                return {
                    "ok": True,
                    "address": address,
                    "port": str(port),
                    "count": _parse_tmsh_connection_count(output, address, str(port)),
                }
            except Exception as e:
                return {
                    "ok": False,
                    "address": address,
                    "port": str(port),
                    "count": 0,
                    "error": str(e)[:200],
                }

    results = await asyncio.gather(*[count_target(address, port) for address, port in targets])
    return sum(item.get("count", 0) for item in results if item.get("ok")), results


def _best_connection_count(stats_count: int, tmsh_count: int) -> int:
    stats_count = int(stats_count or 0)
    tmsh_count = int(tmsh_count or 0)
    if tmsh_count > 0 or stats_count == 0:
        return tmsh_count
    return stats_count


async def find_pools_by_member_tmsh(
    cfg: F5Config,
    ip: str,
    port: str = None,
    client: httpx.AsyncClient = None,
    suffix_ip: bool = False,
):
    cmd = f"tmsh -q list ltm pool recursive one-line | grep -F {shlex.quote(ip)}"

    output = await f5_bash(cfg, cmd, client=client)

    results = []
    seen = set()
    is_full = _is_full_ip(ip)
    if suffix_ip:
        member_re = _re.compile(
            r'(?P<member>\S+:(?P<member_port>\d+))\s+\{(?P<body>[^{}]*\baddress\s+(?:\S+/)*(?:\d{1,3}\.)*'
            + _re.escape(ip)
            + r'(?:%\d+)?(?=\s|$)[^{}]*)\}'
        )
    elif is_full:
        member_re = _re.compile(
            r'(?P<member>\S+:(?P<member_port>\d+))\s+\{(?P<body>[^{}]*\baddress\s+(?:\S+/)*'
            + _re.escape(ip)
            + r'(?:%\d+)?\b[^{}]*)\}'
        )
    else:
        member_re = _re.compile(
            r'(?P<member>\S+:(?P<member_port>\d+))\s+\{(?P<body>[^{}]*\baddress\s+\S*'
            + _re.escape(ip)
            + r'\S*(?:%\d+)?\b[^{}]*)\}'
        )

    for line in output.splitlines():
        line = line.strip()

        if not line:
            continue

        if not line.startswith("ltm pool "):
            continue

        if ip not in line:
            continue

        parts = line.split()

        if len(parts) < 3:
            continue

        full_pool = parts[2]

        if full_pool.startswith("/"):
            arr = full_pool.strip("/").split("/")
            partition = arr[0] if len(arr) > 1 else "Common"
            pool_name = arr[-1]
        else:
            partition = "Common"
            pool_name = full_pool

        members = []
        for match in member_re.finditer(line):
            member_port = match.group("member_port")
            if not _port_matches(port, member_port):
                continue

            member_name = match.group("member")
            member_body = match.group("body")
            members.append({
                "name": member_name,
                "address": ip,
                "port": member_port,
                "state": _tmsh_member_state(member_body),
                "session": "",
                "nodeState": "unknown",
            })

        if not members:
            continue

        key = f"{partition}/{pool_name}"
        if key in seen:
            continue

        seen.add(key)
        results.append({
            "partition": partition,
            "pool": pool_name,
            "members": members,
        })

    return results


async def find_pools_by_member_rest(
    cfg: F5Config,
    ip: str,
    port: str = None,
    pool_keys: set = None,
    client: httpx.AsyncClient = None,
    suffix_ip: bool = False,
):
    try:
        pool_data = await f5_get(
            cfg,
            "ltm/pool?expandSubcollections=true&$select=name,partition,membersReference&$top=5000",
            client=client,
        )
    except Exception as e:
        logging.debug(f"Expanded pool member lookup failed: {e}")
        if not pool_keys:
            raise
        pool_data = {"items": [
            {"partition": key.split("/", 1)[0], "name": key.split("/", 1)[1]}
            for key in pool_keys
            if "/" in key
        ]}

    results = []
    seen = set()
    saw_expanded_members = False

    for pool in pool_data.get("items", []):
        pool_name = pool.get("name", "")
        partition = pool.get("partition", "Common")
        key = f"{partition}/{pool_name}"

        if pool_keys and key not in pool_keys:
            continue

        members = pool.get("membersReference", {}).get("items", [])
        if members:
            saw_expanded_members = True

        for member in members:
            if not _address_matches(ip, member.get("address", ""), member.get("name", ""), suffix_only=suffix_ip):
                continue
            if not _port_matches(port, _member_port(member)):
                continue
            if key not in seen:
                seen.add(key)
                results.append({"partition": partition, "pool": pool_name})

    if saw_expanded_members or results:
        return results

    pools_to_check = []
    for pool in pool_data.get("items", []):
        pool_name = pool.get("name", "")
        partition = pool.get("partition", "Common")
        key = f"{partition}/{pool_name}"
        if pool_keys and key not in pool_keys:
            continue
        pools_to_check.append((partition, pool_name))

    sem = asyncio.Semaphore(20)

    async def fetch_members(partition: str, pool_name: str):
        async with sem:
            try:
                data = await f5_get(
                    cfg,
                    f"ltm/pool/~{partition}~{pool_name}/members?$select=name,address,port",
                    client=client,
                )
                return partition, pool_name, data.get("items", [])
            except Exception as e:
                logging.debug(f"Pool member lookup failed for {partition}/{pool_name}: {e}")
                return partition, pool_name, []

    member_results = await asyncio.gather(
        *[fetch_members(partition, pool_name) for partition, pool_name in pools_to_check]
    )

    for partition, pool_name, members in member_results:
        key = f"{partition}/{pool_name}"
        for member in members:
            if not _address_matches(ip, member.get("address", ""), member.get("name", ""), suffix_only=suffix_ip):
                continue
            if not _port_matches(port, _member_port(member)):
                continue
            if key not in seen:
                seen.add(key)
                results.append({"partition": partition, "pool": pool_name})

    return results


async def topology_cache_lookup(cfg: F5Config, q: str, ip_q: str, port_q: str | None):
    async with AsyncSessionLocal() as db:
        device_res = await db.execute(
            text(
                """
                SELECT id, hostname, last_sync
                FROM devices
                WHERE management_ip = :host
                   OR hostname = :host
                   OR name = :host
                LIMIT 1
                """
            ),
            {"host": cfg.host},
        )
        device = device_res.mappings().first()
        if not device or not device["last_sync"]:
            return set(), set(), False

        params = {
            "device_id": device["id"],
            "hostname": device["hostname"] or "",
            "q_like": f"%{q}%",
            "ip_exact": ip_q,
            "ip_like": f"%{ip_q}%",
            "port_like": f"{port_q or ''}%",
        }

        count_res = await db.execute(
            text(
                """
                SELECT COUNT(*) AS total
                FROM topology_vs_cache
                WHERE device_id = :device_id OR hostname = :hostname
                """
            ),
            params,
        )
        if count_res.mappings().first()["total"] == 0:
            return set(), set(), False

        vs_keys = set()
        pool_keys = set()

        if _looks_like_ip(q):
            if port_q:
                vs_where = "destination_ip = :ip_exact AND destination_port LIKE :port_like"
                member_where = "address = :ip_exact AND port LIKE :port_like"
            else:
                vs_where = "destination_ip LIKE :ip_like"
                member_where = "address LIKE :ip_like"

            vs_res = await db.execute(
                text(
                    f"""
                    SELECT partition, vs_name, pool_partition, pool_name
                    FROM topology_vs_cache
                    WHERE (device_id = :device_id OR hostname = :hostname)
                      AND {vs_where}
                    LIMIT 100
                    """
                ),
                params,
            )
            for row in vs_res.mappings().all():
                vs_keys.add(f"{row['partition']}/{row['vs_name']}")
                if row["pool_name"]:
                    pool_keys.add(f"{row['pool_partition']}/{row['pool_name']}")

            member_res = await db.execute(
                text(
                    f"""
                    SELECT DISTINCT v.partition, v.vs_name, m.partition AS pool_partition, m.pool_name
                    FROM topology_member_cache m
                    JOIN topology_vs_cache v
                      ON v.hostname = m.hostname
                     AND v.pool_partition = m.partition
                     AND v.pool_name = m.pool_name
                    WHERE (m.device_id = :device_id OR m.hostname = :hostname)
                      AND {member_where}
                    LIMIT 100
                    """
                ),
                params,
            )
            for row in member_res.mappings().all():
                vs_keys.add(f"{row['partition']}/{row['vs_name']}")
                pool_keys.add(f"{row['pool_partition']}/{row['pool_name']}")
        else:
            vs_res = await db.execute(
                text(
                    """
                    SELECT partition, vs_name, pool_partition, pool_name
                    FROM topology_vs_cache
                    WHERE (device_id = :device_id OR hostname = :hostname)
                      AND (lower(vs_name) LIKE :q_like OR lower(pool_name) LIKE :q_like)
                    LIMIT 100
                    """
                ),
                params,
            )
            for row in vs_res.mappings().all():
                vs_keys.add(f"{row['partition']}/{row['vs_name']}")
                if row["pool_name"]:
                    pool_keys.add(f"{row['pool_partition']}/{row['pool_name']}")

        return vs_keys, pool_keys, True


async def auto_sync_device_for_host(host: str) -> None:
    host_key = (host or "").strip().lower()
    if not host_key:
        return

    async with _AUTO_SYNC_LOCK:
        if host_key in _AUTO_SYNC_IN_PROGRESS:
            return
        _AUTO_SYNC_IN_PROGRESS.add(host_key)

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Device).where(
                    (Device.management_ip == host)
                    | (Device.hostname == host)
                    | (Device.name == host)
                ).limit(1)
            )
            device = result.scalars().first()
            if not device:
                logging.info(f"Auto-sync skipped: device not found for {host}")
                return

            logging.info(f"Auto-sync started after topology cache miss: {device.name}")
            await sync_one_device(db, device)
            logging.info(f"Auto-sync finished after topology cache miss: {device.name}")
    except Exception as e:
        logging.warning(f"Auto-sync after topology cache miss failed for {host}: {e}")
    finally:
        async with _AUTO_SYNC_LOCK:
            _AUTO_SYNC_IN_PROGRESS.discard(host_key)


def schedule_auto_sync_after_cache_miss(cfg: F5Config) -> None:
    try:
        asyncio.create_task(auto_sync_device_for_host(cfg.host))
    except RuntimeError as e:
        logging.warning(f"Unable to schedule auto-sync after topology cache miss: {e}")


async def fetch_virtuals_by_keys(
    cfg: F5Config,
    vs_keys: set,
    client: httpx.AsyncClient,
) -> list[dict]:
    if not vs_keys:
        return []

    sem = asyncio.Semaphore(12)

    async def fetch_one(key: str):
        partition, vs_name = key.split("/", 1)
        vs_encoded = quote(vs_name.replace("/", "~"), safe="~")
        async with sem:
            try:
                return await f5_get(
                    cfg,
                    f"ltm/virtual/~{quote(partition, safe='')}~{vs_encoded}?$select=name,destination,partition,enabled,disabled,pool,rules,description,ipProtocol,sourceAddressTranslation",
                    client=client,
                )
            except Exception as e:
                logging.debug(f"Cached VS detail fetch failed for {key}: {e}")
                return None

    results = await asyncio.gather(*[fetch_one(key) for key in vs_keys])
    return [item for item in results if isinstance(item, dict) and item.get("name")]


@app.post("/api/search-unified")
async def search_unified(cfg: F5Config, q: str):
    t0 = time.time()

    try:
        q = q.strip().lower()
        suffix_ip = ":" in q
        ip_q, port_q = _parse_ip_port(q) if _looks_like_ip(q) else (q, None)
        cache_vs_keys, cache_pool_keys, cache_checked = await topology_cache_lookup(cfg, q, ip_q, port_q)
        cache_hit = bool(cache_vs_keys)
        search_source = "topology-cache" if cache_hit else ("cache-miss-f5" if cache_checked else "f5")

        async with httpx.AsyncClient(verify=cfg.verify_ssl, timeout=20.0) as client:
            # Stage 1: Run initial network fetches in parallel.
            if cache_hit:
                vs_task = fetch_virtuals_by_keys(cfg, cache_vs_keys, client=client)
                pool_task = None
            else:
                vs_task = f5_get(
                    cfg,
                    "ltm/virtual?$select=name,destination,partition,enabled,pool,rules,description,ipProtocol,sourceAddressTranslation&$top=5000",
                    client=client
                )
                pool_task = f5_get(
                    cfg,
                    "ltm/pool?$select=name,partition&$top=5000",
                    client=client
                )
            node_task = fetch_node_map(cfg, client=client)

            member_task = None
            if _looks_like_ip(q) and not cache_hit:
                member_task = find_pools_by_member_tmsh(
                    cfg,
                    ip_q,
                    port_q,
                    client=client,
                    suffix_ip=suffix_ip,
                )

            # Gather Stage 1
            if member_task:
                vs_res, pool_res, node_res, matches = await asyncio.gather(
                    vs_task, pool_task, node_task, member_task, return_exceptions=True
                )
                if isinstance(matches, Exception):
                    logging.warning(f"find_pools_by_member_tmsh failed: {matches}")
                    matches = []
            elif cache_hit:
                vs_res, node_res = await asyncio.gather(
                    vs_task, node_task, return_exceptions=True
                )
                pool_res = {"items": []}
                matches = []
            else:
                vs_res, pool_res, node_res = await asyncio.gather(
                    vs_task, pool_task, node_task, return_exceptions=True
                )
                matches = []

            # Check core vs_data success
            if isinstance(vs_res, Exception):
                raise vs_res
            vs_data = {"items": vs_res} if cache_hit else vs_res

            # Check pool_data success
            if isinstance(pool_res, Exception):
                logging.debug(f"Failed to fetch pools: {pool_res}")
                pool_data = {"items": []}
            else:
                pool_data = pool_res

            # Check node_map success
            node_map = node_res if not isinstance(node_res, Exception) else {}

            all_vs = vs_data.get("items", [])
            if cache_hit and not all_vs:
                logging.info("Topology cache had candidates but no VS detail was returned; falling back to full F5 search")
                cache_hit = False
                search_source = "database-stale-f5"
                vs_res, pool_res = await asyncio.gather(
                    f5_get(
                        cfg,
                        "ltm/virtual?$select=name,destination,partition,enabled,pool,rules,description,ipProtocol,sourceAddressTranslation&$top=5000",
                        client=client,
                    ),
                    f5_get(
                        cfg,
                        "ltm/pool?$select=name,partition&$top=5000",
                        client=client,
                    ),
                    return_exceptions=True,
                )
                if isinstance(vs_res, Exception):
                    raise vs_res
                vs_data = vs_res
                pool_data = pool_res if not isinstance(pool_res, Exception) else {"items": []}
                all_vs = vs_data.get("items", [])
                if _looks_like_ip(q):
                    try:
                        matches = await find_pools_by_member_tmsh(
                            cfg,
                            ip_q,
                            port_q,
                            client=client,
                            suffix_ip=suffix_ip,
                        )
                    except Exception as e_tmsh:
                        logging.warning(f"find_pools_by_member_tmsh failed: {e_tmsh}")
                        matches = []

            matched_vs_keys = set()
            member_pool_keys = set()
            pool_to_vs = {}

            for vs in all_vs:
                pool_ref = vs.get("pool", "")
                if pool_ref:
                    pool_partition, pool_name = _pool_ref_parts(pool_ref, vs.get("partition", "Common"))
                    key = f"{pool_partition}/{pool_name}"
                    pool_to_vs.setdefault(key, []).append(vs)

            if cache_hit:
                matched_vs_keys.update(cache_vs_keys)
                member_pool_keys.update(cache_pool_keys)
            else:
                # 1. Search by VS name
                for vs in all_vs:
                    name = vs.get("name", "").lower()
                    if q in name:
                        matched_vs_keys.add(f"{vs.get('partition')}/{vs.get('name')}")

                # 2. Search by Pool name
                all_pools = pool_data.get("items", [])
                for p in all_pools:
                    pool_name = p.get("name", "").lower()
                    partition = p.get("partition", "Common")
                    if q in pool_name:
                        pkey = f"{partition}/{pool_name}"
                        for vs in pool_to_vs.get(pkey, []):
                            matched_vs_keys.add(f"{vs.get('partition')}/{vs.get('name')}")

                # 3. Search by IP / Port (if matches IP pattern)
                if _looks_like_ip(q):
                    for vs in all_vs:
                        dest = vs.get("destination", "").lower()
                        clean_dest = dest.split("/")[-1]
                        dest_ip, dest_port = _parse_ip_port(clean_dest)
                        dest_ip_clean = dest_ip.split("%", 1)[0] if "%" in dest_ip else dest_ip
                        ip_match = _ip_matches(ip_q, dest_ip_clean, suffix_only=suffix_ip)

                        if ip_match:
                            if port_q:
                                if dest_port and dest_port.startswith(port_q):
                                    matched_vs_keys.add(f"{vs.get('partition')}/{vs.get('name')}")
                            else:
                                matched_vs_keys.add(f"{vs.get('partition')}/{vs.get('name')}")

                    # Fallback to REST search if TMSH returned no results
                    if not matches:
                        try:
                            matches = await find_pools_by_member_rest(
                                cfg,
                                ip_q,
                                port_q,
                                client=client,
                                suffix_ip=suffix_ip,
                            )
                        except Exception as e_rest:
                            logging.warning(f"find_pools_by_member_rest failed: {e_rest}")

                    for m in matches:
                        pkey = f"{m['partition']}/{m['pool']}"
                        member_pool_keys.add(pkey)
                        for vs in pool_to_vs.get(pkey, []):
                            matched_vs_keys.add(f"{vs.get('partition')}/{vs.get('name')}")

            matched_vs = [
                vs for vs in all_vs
                if f"{vs.get('partition')}/{vs.get('name')}" in matched_vs_keys
            ]

            if not matched_vs:
                elapsed = round(time.time() - t0, 2)
                return {
                    "vsList": [],
                    "q": q,
                    "searchType": "unified",
                    "searchSource": search_source,
                    "elapsed": elapsed
                }

            matched_pool_keys = set()
            matched_vs_key_set = set()
            for vs in matched_vs:
                matched_vs_key_set.add(f"{vs.get('partition')}/{vs.get('name')}")
                if not vs.get("pool"):
                    continue

                pool_partition, pool_name = _pool_ref_parts(
                    vs.get("pool", ""),
                    vs.get("partition", "Common"),
                )
                if pool_name:
                    matched_pool_keys.add(f"{pool_partition}/{pool_name}")

            # Stage 2: Fetch pool details and VS extras in parallel
            pool_detail_task = None
            pools_to_fetch = matched_pool_keys or member_pool_keys
            if pools_to_fetch:
                pool_detail_task = fetch_pool_details_bulk(
                    cfg,
                    pools_to_fetch,
                    node_map=node_map,
                    client=client,
                    fallback_missing=len(pools_to_fetch) <= 50,
                )

            profile_resolver = ProfileTypeResolver(cfg, client=client)
            vs_extra_task = None
            skip_vs_extra = True
            vs_keys_missing_rules = {
                f"{vs.get('partition')}/{vs.get('name')}"
                for vs in matched_vs
                if not isinstance(vs.get("rules"), list)
            }
            if vs_keys_missing_rules:
                vs_extra_task = fetch_vs_extras_targeted(
                    cfg,
                    vs_keys_missing_rules,
                    client=client,
                    profile_resolver=profile_resolver,
                    include_tls=False,
                    include_profiles=False,
                )

            # Gather Stage 2
            pool_detail_map = {}
            vs_extra_map = {}
            if pool_detail_task and vs_extra_task:
                pool_res, vs_res = await asyncio.gather(
                    pool_detail_task, vs_extra_task, return_exceptions=True
                )
                pool_detail_map = pool_res if not isinstance(pool_res, Exception) else {}
                vs_extra_map = vs_res if not isinstance(vs_res, Exception) else {}
            elif pool_detail_task:
                res = await pool_detail_task
                pool_detail_map = res if not isinstance(res, Exception) else {}
            elif vs_extra_task:
                res = await vs_extra_task
                vs_extra_map = res if not isinstance(res, Exception) else {}

            tasks = [
                build_vs_result(
                    cfg,
                    vs,
                    node_map,
                    client=client,
                    profile_resolver=profile_resolver,
                    pool_detail_map=pool_detail_map,
                    vs_extra_map=vs_extra_map,
                    skip_vs_extra=skip_vs_extra,
                )
                for vs in matched_vs
            ]

            result = await asyncio.gather(*tasks)
            elapsed = round(time.time() - t0, 2)
            if q and not cache_hit and result:
                schedule_auto_sync_after_cache_miss(cfg)

            return {
                "vsList": list(result),
                "q": q,
                "searchType": "unified",
                "searchSource": search_source,
                "elapsed": elapsed
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vs-extra")
async def get_vs_extra(req: VSExtraRequest):
    cfg_obj = F5Config(
        host=req.host,
        username=req.username,
        password=req.password,
        verify_ssl=req.verify_ssl,
    )

    try:
        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=20.0) as client:
            profile_resolver = ProfileTypeResolver(cfg_obj, client=client)
            rules, profiles = await fetch_vs_extra_rest(
                cfg_obj,
                req.partition or "Common",
                req.vs_name,
                client=client,
                profile_resolver=profile_resolver,
                include_tls=True,
                include_profiles=True,
            )
        return {"ok": True, "rules": rules, "profiles": profiles}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _rule_ref_parts(rule_ref: str, default_partition: str = "Common") -> tuple[str, str]:
    rule_ref = (rule_ref or "").strip()
    if rule_ref.startswith("/"):
        parts = rule_ref.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0] or default_partition, parts[-1]
    return default_partition or "Common", rule_ref.split("/")[-1]


async def fetch_irule_content(
    cfg: F5Config,
    partition: str,
    rule_name: str,
    client: httpx.AsyncClient = None,
) -> dict:
    parsed_partition, parsed_name = _rule_ref_parts(rule_name, partition or "Common")
    candidates = [(parsed_partition, parsed_name)]
    if parsed_partition != "Common":
        candidates.append(("Common", parsed_name))

    for rule_partition, rule_leaf in candidates:
        if not rule_leaf:
            continue

        encoded_partition = quote(rule_partition, safe="")
        encoded_rule = quote(rule_leaf.replace("/", "~"), safe="~")
        data = await f5_get(
            cfg,
            f"ltm/rule/~{encoded_partition}~{encoded_rule}",
            client=client,
        )
        content = data.get("apiAnonymous") if isinstance(data, dict) else None
        if content is not None:
            return {
                "partition": rule_partition,
                "name": rule_leaf,
                "fullPath": data.get("fullPath") or f"/{rule_partition}/{rule_leaf}",
                "content": content,
            }

    raise HTTPException(status_code=404, detail=f"iRule {rule_name} was not found")


@app.post("/api/irule-content")
async def get_irule_content(req: IRuleContentRequest):
    cfg_obj = F5Config(
        host=req.host,
        username=req.username,
        password=req.password,
        verify_ssl=req.verify_ssl,
    )

    try:
        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=20.0) as client:
            rule = await fetch_irule_content(
                cfg_obj,
                req.partition or "Common",
                req.rule_name,
                client=client,
            )
        return {"ok": True, **rule}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/health")
async def get_health(cfg: F5Config):
    try:
        vs_data, pool_data = await asyncio.gather(
            f5_get_collection_items(cfg, "ltm/virtual", "name,partition,enabled,disabled"),
            f5_get_collection_items(cfg, "ltm/pool", "name,partition"),
        )
        vs_items = vs_data if isinstance(vs_data, list) else []
        pool_items = pool_data if isinstance(pool_data, list) else []
        vs_up = sum(1 for v in vs_items if v.get("enabled", False) and not v.get("disabled", False))
        return {
            "status": "ok",
            "summary": {
                "totalVS": len(vs_items),
                "vsUp": vs_up,
                "vsDown": len(vs_items) - vs_up,
                "totalPools": len(pool_items),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test-connection")
async def test_connection(cfg: F5Config):
    try:
        data = await f5_get(cfg, "sys/version")
        hostname = ""
        version = ""
        for k, v in data.get("entries", {}).items():
            nested = v.get("nestedStats", {}).get("entries", {})
            if "Version" in nested:
                version = nested["Version"].get("description", "")
                break
        try:
            settings = await f5_get(cfg, "sys/global-settings")
            hostname = settings.get("hostname") or ""
        except Exception:
            hostname = ""
        return {"ok": True, "version": version, "host": cfg.host, "hostname": hostname}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except Exception as e:
        if isinstance(e, httpx.TimeoutException):
            error = "Connection to F5 timed out"
        elif isinstance(e, httpx.ConnectError):
            error = "Cannot connect to F5"
        else:
            error = str(e) or e.__class__.__name__ or "Connection failed"
        return {"ok": False, "error": error}


@app.post("/api/member-action")
async def member_action(req: MemberActionRequest):
    try:
        if req.action == "enable":
            payload = {"session": "user-enabled", "state": "user-up"}
        elif req.action == "force-offline":
            payload = {"session": "user-disabled", "state": "user-down"}
        else:
            raise HTTPException(status_code=400, detail="Action must be 'enable' or 'force-offline'")

        cfg_obj = F5Config(host=req.host, username=req.username, password=req.password, verify_ssl=req.verify_ssl)
        headers = get_headers(cfg_obj)
        member_encoded = req.member_name.replace(":", "%3A")
        urls_to_try = [
            f"https://{req.host}/mgmt/tm/ltm/pool/~{req.partition}~{req.pool_name}/members/~{req.partition}~{member_encoded}",
            f"https://{req.host}/mgmt/tm/ltm/pool/~{req.partition}~{req.pool_name}/members/{member_encoded}",
        ]
        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=15.0) as client:
            last_body = ""
            for url in urls_to_try:
                r = await client.patch(url, headers=headers, json=payload)
                last_body = r.text
                if r.status_code == 401:
                    raise HTTPException(status_code=401, detail="Unauthorized")
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                return {"ok": True, "action": req.action, "member": req.member_name}
            raise HTTPException(status_code=404, detail=f"Member not found. {last_body[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/member-action-bulk")
async def member_action_bulk(req: BulkMemberActionRequest):
    try:
        if req.action == "enable":
            payload = {"session": "user-enabled", "state": "user-up"}
        elif req.action == "force-offline":
            payload = {"session": "user-disabled", "state": "user-down"}
        else:
            raise HTTPException(
                status_code=400,
                detail="Action must be 'enable' or 'force-offline'"
            )

        cfg_obj = F5Config(
            host=req.host,
            username=req.username,
            password=req.password,
            verify_ssl=req.verify_ssl
        )

        headers = get_headers(cfg_obj)

        async with httpx.AsyncClient(
            verify=req.verify_ssl,
            timeout=20.0,
            headers=headers
        ) as client:

            async def patch_member(item):
                partition = item.get("partition", "Common")
                pool_name = item.get("pool_name")
                member_name = item.get("member_name")

                if not pool_name or not member_name:
                    return {
                        "ok": False,
                        "member": member_name,
                        "error": "Incomplete pool/member data"
                    }

                member_encoded = member_name.replace(":", "%3A")

                urls_to_try = [
                    f"https://{req.host}/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/~{partition}~{member_encoded}",
                    f"https://{req.host}/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/{member_encoded}",
                ]

                last_body = ""

                for url in urls_to_try:
                    try:
                        r = await client.patch(url, json=payload)
                        last_body = r.text

                        if r.status_code == 404:
                            continue

                        if r.status_code == 401:
                            return {
                                "ok": False,
                                "member": member_name,
                                "error": "Unauthorized"
                            }

                        r.raise_for_status()

                        return {
                            "ok": True,
                            "member": member_name,
                            "pool": pool_name,
                            "action": req.action
                        }

                    except Exception as e:
                        last_body = str(e)

                return {
                    "ok": False,
                    "member": member_name,
                    "pool": pool_name,
                    "error": last_body[:200]
                }

            sem = asyncio.Semaphore(10)

            async def guarded_patch(item):
                async with sem:
                    return await patch_member(item)

            results = await asyncio.gather(
                *[guarded_patch(m) for m in req.members]
            )

        success = sum(1 for r in results if r.get("ok"))
        failed = len(results) - success

        return {
            "ok": failed == 0,
            "success": success,
            "failed": failed,
            "results": results
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/vs-action")
async def vs_action(req: VSActionRequest):
    """Enable or disable a Virtual Server"""
    try:
        if req.action == "enable":
            payload = {"enabled": True}
        elif req.action == "disable":
            payload = {"disabled": True}
        else:
            raise HTTPException(status_code=400, detail="Action must be 'enable' or 'disable'")

        cfg_obj = F5Config(host=req.host, username=req.username, password=req.password, verify_ssl=req.verify_ssl)
        headers = get_headers(cfg_obj)
        url = f"https://{req.host}/mgmt/tm/ltm/virtual/~{req.partition}~{req.vs_name}"

        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=15.0) as client:
            r = await client.patch(url, headers=headers, json=payload)
            if r.status_code == 401:
                raise HTTPException(status_code=401, detail="Unauthorized")
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Virtual Server {req.vs_name} was not found")
            r.raise_for_status()

        return {"ok": True, "action": req.action, "vs": req.vs_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pool-connections")
async def pool_connections(req: ClearPoolConnectionsRequest):
    try:
        cfg_obj = F5Config(
            host=req.host,
            username=req.username,
            password=req.password,
            verify_ssl=req.verify_ssl,
        )

        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=30.0) as client:
            stats_data, members_data, member_stats_data = await asyncio.gather(
                f5_get(cfg_obj, f"ltm/pool/~{req.partition}~{req.pool_name}/stats", client=client),
                f5_get(
                    cfg_obj,
                    f"ltm/pool/~{req.partition}~{req.pool_name}/members?$select=name,address,port",
                    client=client,
                ),
                f5_get(cfg_obj, f"ltm/pool/~{req.partition}~{req.pool_name}/members/stats", client=client),
                return_exceptions=True,
            )

            if isinstance(members_data, Exception):
                raise members_data

            stats_count = _stat_number(
                stats_data if isinstance(stats_data, dict) else {},
                CONNECTION_STAT_NAMES,
            )
            if isinstance(member_stats_data, dict):
                stats_count = max(stats_count, _stat_number(member_stats_data, CONNECTION_STAT_NAMES))

            targets, skipped = _pool_member_targets(members_data)
            tmsh_count, _ = await _count_member_connections(cfg_obj, targets, client=client)

        return {
            "ok": True,
            "pool": req.pool_name,
            "partition": req.partition,
            "current_connections": _best_connection_count(stats_count, tmsh_count),
            "stats_connections": stats_count,
            "tmsh_connections": tmsh_count,
            "targets": len(targets),
            "skipped": skipped,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/clear-pool-connections")
async def clear_pool_connections(req: ClearPoolConnectionsRequest):
    try:
        cfg_obj = F5Config(
            host=req.host,
            username=req.username,
            password=req.password,
            verify_ssl=req.verify_ssl,
        )

        async with httpx.AsyncClient(verify=req.verify_ssl, timeout=30.0) as client:
            before_stats, members_data, before_member_stats = await asyncio.gather(
                f5_get(cfg_obj, f"ltm/pool/~{req.partition}~{req.pool_name}/stats", client=client),
                f5_get(
                    cfg_obj,
                    f"ltm/pool/~{req.partition}~{req.pool_name}/members?$select=name,address,port",
                    client=client,
                ),
                f5_get(cfg_obj, f"ltm/pool/~{req.partition}~{req.pool_name}/members/stats", client=client),
                return_exceptions=True,
            )

            if isinstance(members_data, Exception):
                raise members_data

            before_stats_count = _stat_number(
                before_stats if isinstance(before_stats, dict) else {},
                CONNECTION_STAT_NAMES,
            )
            if isinstance(before_member_stats, dict):
                before_stats_count = max(
                    before_stats_count,
                    _stat_number(before_member_stats, CONNECTION_STAT_NAMES),
                )

            targets, skipped = _pool_member_targets(members_data)
            before_tmsh, _ = await _count_member_connections(cfg_obj, targets, client=client)
            before = _best_connection_count(before_stats_count, before_tmsh)

            results = []
            for address, port in targets:
                cmd = (
                    "tmsh -q delete sys connection "
                    f"ss-server-addr {shlex.quote(address)} "
                    f"ss-server-port {shlex.quote(str(port))}"
                )
                try:
                    output = await f5_bash(cfg_obj, cmd, client=client)
                    results.append({
                        "ok": True,
                        "address": address,
                        "port": str(port),
                        "output": output.strip()[:200],
                    })
                except Exception as e:
                    results.append({
                        "ok": False,
                        "address": address,
                        "port": str(port),
                        "error": str(e)[:200],
                    })

            await asyncio.sleep(0.3)
            after_stats, after_member_stats = await asyncio.gather(
                f5_get(
                    cfg_obj,
                    f"ltm/pool/~{req.partition}~{req.pool_name}/stats",
                    client=client,
                ),
                f5_get(cfg_obj, f"ltm/pool/~{req.partition}~{req.pool_name}/members/stats", client=client),
                return_exceptions=True,
            )
            after_stats_count = _stat_number(
                after_stats if isinstance(after_stats, dict) else {},
                CONNECTION_STAT_NAMES,
            )
            if isinstance(after_member_stats, dict):
                after_stats_count = max(
                    after_stats_count,
                    _stat_number(after_member_stats, CONNECTION_STAT_NAMES),
                )
            after_tmsh, _ = await _count_member_connections(cfg_obj, targets, client=client)
            after = _best_connection_count(after_stats_count, after_tmsh)

        failed = sum(1 for item in results if not item.get("ok"))
        return {
            "ok": failed == 0,
            "pool": req.pool_name,
            "partition": req.partition,
            "before": before,
            "after": after,
            "attempted": len(results),
            "failed": failed,
            "skipped": skipped,
            "before_stats": before_stats_count,
            "after_stats": after_stats_count,
            "results": results,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
