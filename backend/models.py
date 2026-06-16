"""
SQLAlchemy ORM models for device management and inventory lookup.

Tables:
  devices      - saved F5 devices, management IPs, and credentials
  inventory_ip - synced IP records from each F5 hostname
  topology_vs_cache - synced Virtual Server search index for Topology
  topology_member_cache - synced Pool Member search index for Topology

Constraint:
  inventory_ip: UNIQUE(hostname, ip, port, type)
  - Duplicate IPs are allowed across different hostnames.
  - The same IP can exist in one hostname as different record types.
  - The same hostname + IP + port + type combination is not allowed twice.
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from backend.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    management_ip = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False, default="admin")
    password_encrypted = Column(Text, nullable=False)
    verify_ssl = Column(Boolean, default=False, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    last_sync = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)
    last_error = Column(Text, nullable=True)
    hostname = Column(String(255), nullable=True)

    def __repr__(self):
        return f"<Device id={self.id} name={self.name!r} ip={self.management_ip!r}>"


class InventoryIP(Base):
    __tablename__ = "inventory_ip"

    __table_args__ = (
        UniqueConstraint("hostname", "ip", "port", "type", name="uq_hostname_ip_port_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, nullable=True, index=True)
    hostname = Column(String(255), nullable=False, index=True)
    ip = Column(String(64), nullable=False, index=True)
    port = Column(String(16), nullable=False, default="")
    type = Column(String(32), nullable=False)

    def __repr__(self):
        return f"<InventoryIP hostname={self.hostname!r} ip={self.ip!r} type={self.type!r}>"


class TopologyVSCache(Base):
    __tablename__ = "topology_vs_cache"

    __table_args__ = (
        UniqueConstraint("hostname", "partition", "vs_name", name="uq_topology_vs_cache"),
    )

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, nullable=True, index=True)
    hostname = Column(String(255), nullable=False, index=True)
    partition = Column(String(255), nullable=False, default="Common")
    vs_name = Column(String(255), nullable=False, index=True)
    destination = Column(String(255), nullable=False, default="")
    destination_ip = Column(String(64), nullable=False, default="", index=True)
    destination_port = Column(String(16), nullable=False, default="", index=True)
    pool_partition = Column(String(255), nullable=False, default="Common")
    pool_name = Column(String(255), nullable=False, default="", index=True)
    enabled = Column(Boolean, default=True, nullable=False)


class TopologyMemberCache(Base):
    __tablename__ = "topology_member_cache"

    __table_args__ = (
        UniqueConstraint("hostname", "partition", "pool_name", "member_name", name="uq_topology_member_cache"),
    )

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, nullable=True, index=True)
    hostname = Column(String(255), nullable=False, index=True)
    partition = Column(String(255), nullable=False, default="Common")
    pool_name = Column(String(255), nullable=False, index=True)
    member_name = Column(String(255), nullable=False)
    address = Column(String(64), nullable=False, index=True)
    port = Column(String(16), nullable=False, default="", index=True)
