"""
Inventory IP search, export, and management endpoints.
"""
import io
import zipfile
from html import escape
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import InventoryIPOut, InventorySearchResult

router = APIRouter(prefix="/inventory", tags=["inventory"])

EXPORT_COLUMNS = (
    ("hostname", "Hostname"),
    ("ip", "IP"),
    ("port", "Port"),
    ("type", "Type"),
)


def _format_export_value(value) -> str:
    if value is None:
        return ""
    if value == "VS":
        return "Virtual Server"
    return str(value)


def _parse_ip_port_query(value: str) -> tuple[str, Optional[str]]:
    value = value.strip()
    if ":" not in value:
        return value, None

    ip, port = value.rsplit(":", 1)
    ip = ip.strip()
    port = port.strip()
    if not ip or not port:
        return value, None

    return ip, port


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value.strip())
    return cleaned.strip("-") or "all"


async def _inventory_export_rows(
    db: AsyncSession,
    device_id: Optional[int] = None,
):
    params = {}
    filters = []

    if device_id is not None:
        filters.append(
            """
            (
                inv.device_id = :device_id
                OR inv.hostname = (
                SELECT hostname
                FROM devices
                WHERE id = :device_id
                )
            )
            """
        )
        params["device_id"] = device_id

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    result = await db.execute(
        text(
            f"""
            SELECT
                inv.hostname AS hostname,
                inv.ip AS ip,
                inv.port AS port,
                inv.type AS type
            FROM inventory_ip inv
            {where_clause}
            ORDER BY inv.hostname, inv.type, inv.ip
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


def _xlsx_col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_inline_cell(row_idx: int, col_idx: int, value: str) -> str:
    cell_ref = f"{_xlsx_col_name(col_idx)}{row_idx}"
    return (
        f'<c r="{cell_ref}" t="inlineStr">'
        f"<is><t>{escape(value)}</t></is>"
        "</c>"
    )


def _xlsx_response(rows, filename: str) -> Response:
    table_rows = [[label for _, label in EXPORT_COLUMNS]]
    for row in rows:
        table_rows.append([_format_export_value(row.get(key)) for key, _ in EXPORT_COLUMNS])

    sheet_rows = []
    for row_idx, values in enumerate(table_rows, start=1):
        cells = "".join(
            _xlsx_inline_cell(row_idx, col_idx, value)
            for col_idx, value in enumerate(values, start=1)
        )
        sheet_rows.append(f'<row r="{row_idx}">{cells}</row>')

    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
    <col min="1" max="1" width="24" customWidth="1"/>
    <col min="2" max="2" width="18" customWidth="1"/>
    <col min="3" max="3" width="10" customWidth="1"/>
    <col min="4" max="4" width="16" customWidth="1"/>
  </cols>
  <sheetData>
    {''.join(sheet_rows)}
  </sheetData>
</worksheet>"""

    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Inventory" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": sheet_xml,
    }

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _export_scope_name(
    db: AsyncSession,
    device_id: Optional[int],
) -> str:
    if device_id is not None:
        result = await db.execute(
            text("SELECT COALESCE(hostname, name, management_ip) AS label FROM devices WHERE id = :device_id"),
            {"device_id": device_id},
        )
        row = result.mappings().first()
        return _safe_filename_part(row["label"] if row and row["label"] else f"device-{device_id}")

    return "all"


@router.get("/search", response_model=InventorySearchResult)
async def search_inventory(
    ip: str = Query(..., description="IP address exact match"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search IP records in the inventory database using an exact match.
    This reads only the local database and does not log in to F5.
    """
    ip = ip.strip()
    query_ip, query_port = _parse_ip_port_query(ip)
    if query_port is not None:
        result = await db.execute(
            text(
                """
                SELECT *
                FROM inventory_ip
                WHERE ip = :ip
                  AND port = :port
                ORDER BY hostname, type, port
                """
            ),
            {"ip": query_ip, "port": query_port},
        )
    else:
        result = await db.execute(
            text("SELECT * FROM inventory_ip WHERE ip = :ip ORDER BY hostname, type, port"),
            {"ip": query_ip},
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
    """Return inventory data, optionally filtered by device_id."""
    if device_id is not None:
        dev_res = await db.execute(
            text("SELECT hostname FROM devices WHERE id = :device_id"),
            {"device_id": device_id},
        )
        dev = dev_res.mappings().first()
        if not dev:
            return []

        result = await db.execute(
            text(
                "SELECT * FROM inventory_ip WHERE device_id = :device_id OR hostname = :hostname "
                "ORDER BY type, ip, port LIMIT :limit OFFSET :skip"
            ),
            {"device_id": device_id, "hostname": dev["hostname"] or "", "limit": limit, "skip": skip},
        )
    else:
        result = await db.execute(
            text(
                "SELECT * FROM inventory_ip ORDER BY hostname, type, ip, port "
                "LIMIT :limit OFFSET :skip"
            ),
            {"limit": limit, "skip": skip},
        )
    rows = result.mappings().all()
    return [InventoryIPOut.model_validate(dict(row)) for row in rows]


@router.get("/export.xlsx")
async def export_inventory_xlsx(
    device_id: Optional[int] = Query(None, description="Filter by device ID"),
    db: AsyncSession = Depends(get_db),
):
    """Export inventory to XLSX, optionally filtered by device_id."""
    rows = await _inventory_export_rows(db, device_id=device_id)
    scope = await _export_scope_name(db, device_id)
    return _xlsx_response(rows, f"f5-inventory-{scope}.xlsx")


@router.delete("/clear", status_code=200)
async def clear_inventory(
    device_id: Optional[int] = Query(None, description="Clear only for specific device ID"),
    db: AsyncSession = Depends(get_db),
):
    """Delete inventory_ip data for one device or all devices."""
    if device_id is not None:
        dev_res = await db.execute(
            text("SELECT hostname FROM devices WHERE id = :device_id"),
            {"device_id": device_id},
        )
        dev = dev_res.mappings().first()
        if not dev:
            raise HTTPException(
                status_code=404,
                detail="Device not found",
            )

        await db.execute(
            text("DELETE FROM inventory_ip WHERE device_id = :device_id OR hostname = :hostname"),
            {"device_id": device_id, "hostname": dev["hostname"] or ""},
        )
        await db.commit()
        return {
            "ok": True,
            "message": f"Inventory for device '{dev['hostname']}' was cleared",
        }

    await db.execute(text("DELETE FROM inventory_ip"))
    await db.commit()
    return {"ok": True, "message": "All inventory data was cleared"}
