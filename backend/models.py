"""
models.py — SQLAlchemy ORM models untuk inventory feature.

Tabel:
  devices      — daftar perangkat F5 (management IP, credentials)
  inventory_ip — hasil sync IP (VS / NODE) dari setiap F5 hostname

Constraint:
  inventory_ip: UNIQUE(hostname, ip, type)
  - Boleh duplikat IP antar hostname berbeda
  - Boleh IP yang sama dalam hostname sebagai VS dan NODE sekaligus
  - Tidak boleh hostname + ip + type yang persis sama
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    UniqueConstraint, func,
)
from backend.database import Base


class Device(Base):
    __tablename__ = "devices"

    id                 = Column(Integer, primary_key=True, index=True)
    name               = Column(String(255), nullable=False)
    management_ip      = Column(String(255), nullable=False)
    username           = Column(String(255), nullable=False, default="admin")
    password_encrypted = Column(Text, nullable=False)
    verify_ssl         = Column(Boolean, default=False, nullable=False)
    enabled            = Column(Boolean, default=True, nullable=False)
    last_sync          = Column(DateTime, nullable=True)
    last_status        = Column(String(50), nullable=True)   # OK / FAILED / NEVER
    last_error         = Column(Text, nullable=True)
    hostname           = Column(String(255), nullable=True)

    def __repr__(self):
        return f"<Device id={self.id} name={self.name!r} ip={self.management_ip!r}>"


class InventoryIP(Base):
    __tablename__ = "inventory_ip"

    __table_args__ = (
        UniqueConstraint("hostname", "ip", "type", name="uq_hostname_ip_type"),
    )

    id        = Column(Integer, primary_key=True, index=True)
    hostname  = Column(String(255), nullable=False, index=True)
    ip        = Column(String(64), nullable=False, index=True)
    type      = Column(String(32), nullable=False)   # "VS" | "POOL_MEMBER" | "SELF_IP"
    last_seen = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<InventoryIP hostname={self.hostname!r} ip={self.ip!r} type={self.type!r}>"
