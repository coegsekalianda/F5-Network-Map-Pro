"""
F5 BIG-IP iControl REST client used for inventory sync.

Main methods:
  - test_connection()            : verify login
  - get_hostname()               : read hostname from global-settings
  - get_virtual_server_ips()     : read Virtual Server IPs and skip forwarding VS
  - get_pool_member_ips()        : read Pool Member IPs
  - get_self_ips()               : read Self IPs

Parsing helpers:
  - extract_ip_from_destination(): strip partition, route domain, and port
  - extract_ip_from_address()    : strip partition, route domain, prefix, and port
  - is_forwarding_virtual()      : detect Forwarding VS records
"""
import asyncio
import base64
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_IP_RE = re.compile(
    r"^(?:\S+/)?(\d{1,3}(?:\.\d{1,3}){3})(?:%\d+)?(?:[.:].*)?$"
)


def _basic_auth_header(username: str, password: str) -> dict:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def _clean_port(value) -> str:
    if value is None:
        return ""

    port = str(value).strip()
    if not port or port.lower() in ("any", "all", "*"):
        return ""

    return port


def extract_ip_port_from_value(value: str) -> tuple[Optional[str], str]:
    """
    Extract IPv4 and port from common F5 formats:
      /Common/10.1.2.3:443       -> ("10.1.2.3", "443")
      /Common/10.1.2.3%123:443   -> ("10.1.2.3", "443")
      /Common/10.1.2.3.any       -> ("10.1.2.3", "")
      10.1.2.3/24                -> ("10.1.2.3", "")
    """
    if not value:
        return None, ""

    raw = str(value).strip()
    if raw.startswith("/"):
        raw = raw.split("/", 2)[-1]

    raw = raw.split("/", 1)[0]
    match = re.match(
        r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})(?:%\d+)?(?:(?::|\.)(?P<port>[^/]+))?$",
        raw,
    )
    if not match:
        return None, ""

    return match.group("ip"), _clean_port(match.group("port"))


def extract_ip_from_destination(destination: str) -> Optional[str]:
    """
    Extract only the IP from an F5 destination value.
    Examples:
      /Common/192.168.10.10:443       -> 192.168.10.10
      /Common/192.168.10.10%123:443   -> 192.168.10.10
      192.168.10.10:443               -> 192.168.10.10
      /Common/10.10.10.10.any         -> 10.10.10.10
    """
    ip, _port = extract_ip_port_from_value(destination)
    return ip


def extract_ip_from_node_address(address: str) -> Optional[str]:
    """
    Extract the IP from an F5 node address.
    Supported formats: 10.45.5.160, /Common/10.45.5.160, 10.45.5.160%123.
    """
    if not address:
        return None

    raw = address.strip()

    if raw.startswith("/"):
        raw = raw.split("/")[-1]

    raw = raw.split("%")[0]

    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", raw):
        return raw

    return None


def extract_ip_from_address(value: str) -> Optional[str]:
    """
    Extract IPv4 from common F5 formats:
      /Common/10.1.2.3:80      -> 10.1.2.3
      10.1.2.3%123:80          -> 10.1.2.3
      10.1.2.3/24              -> 10.1.2.3
      10.1.2.3.any             -> 10.1.2.3
    """
    ip, _port = extract_ip_port_from_value(value)
    return ip


def is_forwarding_virtual(vs: dict) -> bool:
    """Return True when the Virtual Server is Forwarding IP or Forwarding L2."""
    vs_type = str(vs.get("type", "")).lower()

    if vs_type == "forwarding-ip" or vs_type == "forwarding-l2":
        return True
    if "forwarding" in vs_type:
        return True

    if vs.get("ipForward") is True or str(vs.get("ipForward")).lower() == "true":
        return True
    if vs.get("l2Forward") is True or str(vs.get("l2Forward")).lower() == "true":
        return True

    return False


class F5Client:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        timeout: float = 15.0,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._headers = _basic_auth_header(username, password)

    def _url(self, path: str) -> str:
        return f"https://{self.host}/mgmt/tm/{path}"

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict:
        url = self._url(path)
        r = await client.get(url, headers=self._headers)
        if r.status_code == 401:
            raise PermissionError(f"Unauthorized. Check username/password for {self.host}")
        if r.status_code == 404:
            return {"items": []}
        r.raise_for_status()
        return r.json()

    async def test_connection(self) -> dict:
        """Test F5 connectivity and return {'ok': True, 'version': '...'}."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=self.timeout) as client:
            try:
                data = await self._get(client, "sys/version")
                version = ""
                for v in data.get("entries", {}).values():
                    nested = v.get("nestedStats", {}).get("entries", {})
                    if "Version" in nested:
                        version = nested["Version"].get("description", "")
                        break
                return {"ok": True, "version": version, "host": self.host}
            except PermissionError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def get_hostname(self, client: httpx.AsyncClient) -> str:
        """Read the F5 hostname from /mgmt/tm/sys/global-settings."""
        try:
            data = await self._get(client, "sys/global-settings")
            return data.get("hostname", self.host)
        except Exception as e:
            logger.warning(f"[{self.host}] get_hostname failed: {e}. Falling back to management IP.")
            return self.host

    async def get_virtual_server_ip_ports(self, client: httpx.AsyncClient) -> tuple[list[dict], int]:
        """
        Read all Virtual Server IP + port records and skip forwarding-ip records.
        Returns (list_records, forwarding_skipped_count).
        """
        data = await self._get(
            client,
            "ltm/virtual?$select=name,destination,type,partition,ipForward,l2Forward&$top=5000"
        )

        records: list[dict] = []
        skipped = 0
        seen: set[tuple[str, str]] = set()

        for vs in data.get("items", []):
            if is_forwarding_virtual(vs):
                skipped += 1
                continue

            dest = vs.get("destination", "")
            ip, port = extract_ip_port_from_value(dest)
            key = (ip, port)
            if ip and key not in seen:
                seen.add(key)
                records.append({"ip": ip, "port": port})

        return records, skipped

    async def get_virtual_server_ips(self, client: httpx.AsyncClient) -> tuple[list[str], int]:
        records, skipped = await self.get_virtual_server_ip_ports(client)
        return [record["ip"] for record in records], skipped

    async def get_node_ips(self, client: httpx.AsyncClient) -> list[str]:
        """Read all Node IPs from /mgmt/tm/ltm/node."""
        data = await self._get(
            client,
            "ltm/node?$select=name,address,partition&$top=5000"
        )

        ips: list[str] = []
        seen: set[str] = set()

        for node in data.get("items", []):
            addr = node.get("address", "") or node.get("name", "")
            ip = extract_ip_from_node_address(addr)
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

        return ips

    async def get_pool_member_ip_ports(self, client: httpx.AsyncClient) -> list[dict]:
        """
        Read unique IP + port records from all pool members using expandSubcollections.
        FQDN pool members and members without IPv4 are skipped.
        """
        try:
            pools_data = await self._get(
                client,
                "ltm/pool?expandSubcollections=true&$select=name,partition,membersReference&$top=5000",
            )
        except Exception as e:
            logger.warning(
                f"[{self.host}] get_pool_member_ips with expandSubcollections failed: {e}. "
                "Falling back to manual per-pool fetch."
            )
            return await self._get_pool_member_ip_ports_fallback(client)

        records: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for pool in pools_data.get("items", []):
            members_ref = pool.get("membersReference", {})
            members = members_ref.get("items", [])
            for member in members:
                ip, parsed_port = extract_ip_port_from_value(member.get("address", ""))
                if not ip:
                    ip, parsed_port = extract_ip_port_from_value(member.get("name", ""))

                port = _clean_port(member.get("port")) or parsed_port
                key = (ip, port)
                if ip and key not in seen:
                    seen.add(key)
                    records.append({"ip": ip, "port": port})

        return records

    async def get_pool_member_ips(self, client: httpx.AsyncClient) -> list[str]:
        records = await self.get_pool_member_ip_ports(client)
        return [record["ip"] for record in records]

    async def _get_pool_member_ip_ports_fallback(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch pool member IP + port records manually with one request per pool."""
        pools_data = await self._get(
            client,
            "ltm/pool?$select=name,partition,fullPath&$top=5000",
        )

        records: list[dict] = []
        seen: set[tuple[str, str]] = set()

        sem = asyncio.Semaphore(10)

        async def fetch_members(pool: dict) -> list[dict]:
            name = pool.get("name", "")
            partition = pool.get("partition", "Common")
            if not name:
                return []

            pool_name_encoded = name.replace("/", "~")
            path = (
                f"ltm/pool/~{partition}~{pool_name_encoded}/members"
                "?$select=name,address,partition,port&$top=5000"
            )
            try:
                async with sem:
                    data = await self._get(client, path)
                return data.get("items", [])
            except Exception as e:
                logger.warning(f"[{self.host}] get pool members failed for {partition}/{name}: {e}")
                return []

        member_groups = await asyncio.gather(
            *[fetch_members(pool) for pool in pools_data.get("items", [])]
        )

        for members in member_groups:
            for member in members:
                ip, parsed_port = extract_ip_port_from_value(member.get("address", ""))
                if not ip:
                    ip, parsed_port = extract_ip_port_from_value(member.get("name", ""))

                port = _clean_port(member.get("port")) or parsed_port
                key = (ip, port)
                if ip and key not in seen:
                    seen.add(key)
                    records.append({"ip": ip, "port": port})

        return records

    async def get_self_ips(self, client: httpx.AsyncClient) -> list[str]:
        """Read unique Self IP records from /mgmt/tm/net/self."""
        data = await self._get(
            client,
            "net/self?$select=name,address,partition&$top=5000",
        )

        ips: list[str] = []
        seen: set[str] = set()

        for item in data.get("items", []):
            ip = extract_ip_from_address(item.get("address", ""))
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

        return ips
