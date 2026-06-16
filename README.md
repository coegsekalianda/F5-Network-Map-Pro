# F5 Network Map Pro

Web application for F5 BIG-IP topology search, device management, local IP inventory lookup, and Virtual Server connection monitoring. The app reads BIG-IP data through iControl REST, renders Virtual Server -> Pool -> Pool Member relationships, and stores local inventory data in SQLite for fast lookup.

## Key Features

- Topology search by Virtual Server name, Pool name, destination IP, IP:port, Pool Member IP, or Pool Member IP:port.
- Topology search uses synced SQLite cache to find matching Virtual Servers quickly, then loads live details from F5.
- Cache misses and stale cache entries fall back to live F5 search and trigger a background device sync when needed.
- Interactive tree for Virtual Servers, Pools, Pool Members, iRules, status, and connection counts.
- Premium empty, loading, and error states for Topology search.
- Anchored glass detail popups that open beside the selected VS, Pool, Pool Member, or iRule row.
- Virtual Server profile and TLS details are loaded only when the popup is opened.
- iRule content is loaded only when an iRule row is clicked.
- Operational actions from the UI:
  - enable / disable Virtual Server
  - enable / force-offline Pool Member
  - bulk enable / force-offline Pool Member
- Export topology results to PNG and PDF.
- Device Management for saved F5 BIG-IP devices.
- Sync inventory from one device or all enabled devices.
- Sync fetches Virtual Servers, Pool Members, and Self IPs in parallel, then writes database rows in bulk.
- Sync stores IP and port for Virtual Servers and Pool Members.
- Deleting a device also deletes local inventory owned by that device.
- Realtime connection monitoring dashboard for multiple Virtual Servers across multiple F5 devices.
- Device passwords are encrypted with `SECRET_KEY`.

## Stack

- Backend: Python, FastAPI, SQLAlchemy async, SQLite, httpx
- Frontend: HTML, CSS, vanilla JavaScript
- Database: `backend/inventory.db`
- F5 API: iControl REST (`/mgmt/tm/...`)

## Project Structure

```text
f5-network-map-pro/
|-- backend/
|   |-- main.py                   # FastAPI app and topology endpoints
|   |-- database.py               # SQLite async engine and lightweight migrations
|   |-- models.py                 # devices, inventory, and topology cache tables
|   |-- crypto.py                 # device password encryption/decryption
|   |-- routers/
|   |   |-- devices.py            # device CRUD
|   |   |-- inventory.py          # local inventory lookup/export/clear API
|   |   |-- monitoring.py         # VS connection monitoring endpoints
|   |   `-- sync.py               # device inventory sync endpoints
|   |-- services/
|   |   |-- f5_client.py          # iControl REST client for sync
|   |   |-- monitoring_service.py # VS stats polling logic
|   |   `-- sync_service.py       # inventory sync logic
|   `-- requirements.txt
|-- frontend/
|   |-- index.html
|   `-- static/
|       |-- css/app.css
|       `-- js/
|           |-- app.js            # topology UI
|           |-- inventory.js      # devices and inventory UI
|           `-- monitoring.js     # connection monitoring UI
|-- run.sh
`-- README.md
```

## Requirements

- Python 3.9 or newer.
- Network access from the app server to F5 BIG-IP management IPs on port 443.
- F5 user with iControl REST access for reading LTM configuration.
- F5 user with the proper privileges for enable/disable actions.

## Setup

1. Install backend dependencies.

```bash
cd backend
pip install -r requirements.txt
```

2. Create `backend/.env` for the device password encryption secret.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then add the generated key:

```env
SECRET_KEY=your_generated_key
```

`SECRET_KEY` is required for Device Management. If it changes, old saved device passwords cannot be decrypted and must be entered again.

## Run

### Linux / macOS / WSL

```bash
bash run.sh
```

### PowerShell / Windows

```powershell
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open:

```text
http://localhost:8000
```

Monitoring is also available at:

```text
http://localhost:8000/monitoring
```

## Usage

### Topology

1. Open the `Topology` tab.
2. Select a device in the connection field. Saved devices automatically load host, username, password, and SSL settings, then login.
3. Enter a search keyword:
   - Virtual Server name
   - Pool name
   - destination IP, for example `10.1.2.3`
   - IP and port, for example `10.1.2.3:443`
   - Pool Member IP
   - Pool Member IP and port, for example `10.1.2.10:8080`
4. Click `Search`.
5. Click a Virtual Server, Pool, or Member node to open its detail popup.
6. Virtual Server profile and TLS data load when the Virtual Server popup opens.
7. Click an iRule row to load and view the iRule script in a popup.
8. Use `Export PNG` or `Export PDF` when needed.

Run `Sync` for a device to warm the Topology cache. Once synced, Topology can use the database to find matching Virtual Server name, Pool name, destination IP/IP:port, and Pool Member IP/IP:port before loading live details from F5. If the cache has no match, Topology falls back to a live F5 search so newly created objects can still be found before the next sync. When live F5 finds a result that was missing from cache, or when cache points to Virtual Servers that no longer exist, the device is synced automatically in the background.

### Devices

1. Open the `Devices` tab.
2. Click `Add Device`.
3. Fill in name, management IP, username, password, SSL option, and enabled status.
4. Save the device.
5. Use `Test Connection` to validate login.
6. Click `Sync` per device or `Sync All` to pull inventory from F5.
7. Device status changes to `SYNCING` as soon as sync starts.
8. Click `Monitoring` to open `/monitoring`.
9. Deleting a device also deletes its local inventory.

Sync updates both the Inventory table and the Topology cache. To keep large devices responsive, the backend reads the main F5 collections in parallel and uses bulk database writes.

### Inventory

The `Inventory` tab supports IP/IP:port search and XLSX export. The old `Load Inventory`, `Clear Selected Inventory`, and `Clear All Inventory` buttons are no longer shown in the UI.

Stored sync data:

- `VS`: Virtual Server IP and port.
- `POOL_MEMBER`: Pool Member IP and port.
- `SELF_IP`: Self IP with an empty port.

Lookup examples:

- `GET /inventory/search?ip=10.1.2.3`
- `GET /inventory/search?ip=10.1.2.3:443`

Older data may have an empty port. Run device sync again to store port values.

Topology cache data:

- `topology_vs_cache`: Virtual Server name, destination IP/port, partition, and attached Pool.
- `topology_member_cache`: Pool Member IP/port, Pool name, and partition for fast Topology lookup.

These cache tables are refreshed by device sync and are used only to find Topology search candidates quickly. Topology details, status, profiles, TLS, and iRule content are still loaded from F5 when needed.

### Monitoring

1. Open `/monitoring` or click `Monitoring` from `Devices`.
2. In `VS Connection Monitor`, type or select a device hostname.
3. Click `Load Virtual Server`.
4. Select a Virtual Server, optionally enter a custom label, then click `Add VS Monitor`.
5. The dashboard polls connection data every second.
6. Use `Save Dashboard` and `Load Dashboard` to store target lists in browser localStorage.

Realtime monitoring data is fetched directly from F5 iControl REST. F5 credentials stay on the backend; the frontend sends only `device_id`, `partition`, and `vs_name`.

## Cleanup Notes

Generated files such as `__pycache__`, `*.pyc`, SQLite `*.db-shm`, and SQLite `*.db-wal` are not part of the source code. SQLite WAL/SHM files can appear while the server is running and are safe to remove only after the server is stopped.

Source files, comments, docstrings, UI text, and backend-facing messages are kept in English.
