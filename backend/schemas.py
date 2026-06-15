"""
Pydantic request and response schemas.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class DeviceCreate(BaseModel):
    name: str
    management_ip: str
    username: str = "admin"
    password: str
    verify_ssl: bool = False
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    management_ip: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: Optional[bool] = None
    enabled: Optional[bool] = None


class DeviceOut(BaseModel):
    id: int
    name: str
    management_ip: str
    username: str
    verify_ssl: bool
    enabled: bool
    last_sync: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    hostname: Optional[str] = None

    model_config = {"from_attributes": True}


class DeviceTopologyConfig(BaseModel):
    id: int
    name: str
    hostname: Optional[str] = None
    management_ip: str
    username: str
    password: str
    verify_ssl: bool

    model_config = {"from_attributes": True}


class InventoryIPOut(BaseModel):
    id: int
    device_id: Optional[int] = None
    hostname: str
    ip: str
    port: str = ""
    type: str
    last_seen: datetime

    model_config = {"from_attributes": True}


class InventorySearchResult(BaseModel):
    ip: str
    results: List[InventoryIPOut]


class SyncDeviceError(BaseModel):
    device_id: int
    name: str
    management_ip: str
    status: str
    error: str


class SyncResult(BaseModel):
    total_devices: int
    success: int
    failed: int
    vs_ip_synced: int
    node_ip_synced: int
    pool_member_ip_synced: int = 0
    self_ip_synced: int = 0
    forwarding_vs_skipped: int
    errors: List[SyncDeviceError]


class SyncDeviceResult(BaseModel):
    device_id: int
    name: str
    management_ip: str
    hostname: str = ""
    status: str
    vs_ip_synced: int = 0
    node_ip_synced: int = 0
    pool_member_ip_synced: int = 0
    self_ip_synced: int = 0
    forwarding_vs_skipped: int = 0
    error: str = ""
