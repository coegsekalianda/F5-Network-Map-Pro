"""
services/f5_client.py — F5 BIG-IP iControl REST client untuk inventory sync.

Fungsi utama:
  - test_connection()            : verifikasi login
  - get_hostname()               : ambil hostname dari global-settings
  - get_virtual_server_ips()     : ambil IP VS, skip forwarding
  - get_pool_member_ips()        : ambil IP pool member
  - get_self_ips()               : ambil Self IP

Parsing:
  - extract_ip_from_destination(): strip partition, route domain, port
  - extract_ip_from_address()    : strip partition, route domain, prefix, port
  - is_forwarding_virtual()      : deteksi Forwarding VS
"""
import re
import base64
import logging
import asyncio
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

_IP_RE = re.compile(
    r"^(?:\S+/)?(\d{1,3}(?:\.\d{1,3}){3})(?:%\d+)?(?:[.:].*)?$"
)


def _basic_auth_header(username: str, password: str) -> dict:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def extract_ip_from_destination(destination: str) -> Optional[str]:
    """
    Ambil hanya IP dari destination F5. Contoh:
      /Common/192.168.10.10:443       -> 192.168.10.10
      /Common/192.168.10.10%123:443   -> 192.168.10.10
      192.168.10.10:443               -> 192.168.10.10
      /Common/10.10.10.10.any         -> 10.10.10.10
    """
    if not destination:
        return None

    # Buang prefix partition (/Common/, /Partition/)
    raw = destination.strip()
    if raw.startswith("/"):
        raw = raw.split("/", 2)[-1]   # ambil bagian setelah /Partition/

    # Pisah IP dari route domain dan port
    # Format: 10.1.2.3%123:443 atau 10.1.2.3:443 atau 10.1.2.3.any
    # Buang route domain dulu
    raw = raw.split("%")[0]

    # Pisah port (colon atau .any, .all)
    if ":" in raw:
        raw = raw.rsplit(":", 1)[0]
    elif re.search(r"\.\D+$", raw):
        # e.g. 10.10.10.10.any
        raw = re.sub(r"\.\D+$", "", raw)

    # Validasi format IP
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", raw):
        return raw

    return None


def extract_ip_from_node_address(address: str) -> Optional[str]:
    """
    Ambil IP dari node address F5.
    Format: 10.45.5.160, /Common/10.45.5.160, 10.45.5.160%123
    """
    if not address:
        return None

    raw = address.strip()

    # Buang partition prefix
    if raw.startswith("/"):
        raw = raw.split("/")[-1]

    # Buang route domain
    raw = raw.split("%")[0]

    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", raw):
        return raw

    return None


def extract_ip_from_address(value: str) -> Optional[str]:
    """
    Ambil IPv4 dari format umum F5:
      /Common/10.1.2.3:80      -> 10.1.2.3
      10.1.2.3%123:80          -> 10.1.2.3
      10.1.2.3/24              -> 10.1.2.3
      10.1.2.3.any             -> 10.1.2.3
    """
    if not value:
        return None

    raw = str(value).strip()
    if raw.startswith("/"):
        raw = raw.split("/", 2)[-1]

    raw = raw.split("%", 1)[0]
    raw = raw.split("/", 1)[0]

    if ":" in raw:
        raw = raw.rsplit(":", 1)[0]
    elif re.search(r"\.\D+$", raw):
        raw = re.sub(r"\.\D+$", "", raw)

    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", raw):
        return raw

    return None


def is_forwarding_virtual(vs: dict) -> bool:
    """
    Return True jika VS adalah Forwarding IP atau Forwarding L2 — jangan simpan ke inventory.
    """
    vs_type = str(vs.get("type", "")).lower()

    if vs_type == "forwarding-ip" or vs_type == "forwarding-l2":
        return True
    if "forwarding" in vs_type:
        return True

    # Cek boolean flags
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
            raise PermissionError(f"Unauthorized — cek username/password untuk {self.host}")
        if r.status_code == 404:
            return {"items": []}
        r.raise_for_status()
        return r.json()

    async def test_connection(self) -> dict:
        """Test koneksi ke F5, return {'ok': True, 'version': '...'}."""
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
        """
        Ambil hostname F5 dari /mgmt/tm/sys/global-settings.
        Field: hostname
        """
        try:
            data = await self._get(client, "sys/global-settings")
            return data.get("hostname", self.host)
        except Exception as e:
            logger.warning(f"[{self.host}] get_hostname failed: {e}. Fallback ke management IP.")
            return self.host

    async def get_virtual_server_ips(self, client: httpx.AsyncClient) -> tuple[list[str], int]:
        """
        Ambil semua IP Virtual Server, skip forwarding-ip.
        Return: (list_ips, forwarding_skipped_count)
        """
        data = await self._get(
            client,
            "ltm/virtual?$select=name,destination,type,partition,ipForward,l2Forward&$top=5000"
        )

        ips: list[str] = []
        skipped = 0
        seen: set[str] = set()

        for vs in data.get("items", []):
            if is_forwarding_virtual(vs):
                skipped += 1
                continue

            dest = vs.get("destination", "")
            ip = extract_ip_from_destination(dest)
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

        return ips, skipped

    async def get_node_ips(self, client: httpx.AsyncClient) -> list[str]:
        """
        Ambil semua IP Node dari /mgmt/tm/ltm/node.
        """
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

    async def get_pool_member_ips(self, client: httpx.AsyncClient) -> list[str]:
        """
        Ambil IP unik dari semua pool member.
        Pool member FQDN atau member tanpa IPv4 dilewati.
        """
        pools_data = await self._get(
            client,
            "ltm/pool?$select=name,partition,fullPath&$top=5000",
        )

        ips: list[str] = []
        seen: set[str] = set()

        sem = asyncio.Semaphore(10)

        async def fetch_members(pool: dict) -> list[dict]:
            name = pool.get("name", "")
            partition = pool.get("partition", "Common")
            if not name:
                return []

            pool_name_encoded = name.replace("/", "~")
            path = (
                f"ltm/pool/~{partition}~{pool_name_encoded}/members"
                "?$select=name,address,partition&$top=5000"
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
                ip = (
                    extract_ip_from_address(member.get("address", ""))
                    or extract_ip_from_address(member.get("name", ""))
                )
                if ip and ip not in seen:
                    seen.add(ip)
                    ips.append(ip)

        return ips

    async def get_self_ips(self, client: httpx.AsyncClient) -> list[str]:
        """
        Ambil IP unik dari Self IP (/mgmt/tm/net/self).
        """
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
