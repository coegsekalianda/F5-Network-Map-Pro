# F5 Network Map Pro

Web app untuk memetakan topologi F5 BIG-IP dan mengelola inventory IP lintas device. Aplikasi ini membaca data melalui iControl REST, menampilkan relasi Virtual Server -> Pool -> Pool Member, serta menyediakan database inventory lokal berbasis SQLite.

## Fitur Utama

- Search topologi berdasarkan nama Virtual Server, nama Pool, destination IP, atau IP Pool Member.
- Tampilan tree interaktif untuk Virtual Server, Pool, Pool Member, iRule, profile, TLS version, status, dan connection count.
- Action operasional dari UI:
  - enable / disable Virtual Server
  - enable / force-offline Pool Member
  - bulk enable / force-offline Pool Member
  - cek dan clear active connection pada Pool
- Export hasil topologi ke PNG dan PDF.
- Device Management untuk menyimpan daftar F5 BIG-IP.
- Sync inventory dari banyak device ke database lokal.
- Search inventory IP untuk tipe `VS`, `POOL_MEMBER`, dan `SELF_IP`.
- Password device disimpan terenkripsi menggunakan `SECRET_KEY`.

## Stack

- Backend: Python, FastAPI, SQLAlchemy async, SQLite, httpx
- Frontend: HTML, CSS, JavaScript vanilla
- Database: `backend/inventory.db`
- API F5: iControl REST (`/mgmt/tm/...`)

## Struktur Project

```text
f5-network-map-pro/
|-- backend/
|   |-- main.py                 # FastAPI app dan endpoint topology
|   |-- database.py             # SQLite async engine
|   |-- models.py               # tabel devices dan inventory_ip
|   |-- crypto.py               # enkripsi/dekripsi password device
|   |-- routers/
|   |   |-- devices.py          # CRUD device
|   |   |-- inventory.py        # search/clear/list inventory
|   |   `-- sync.py             # sync inventory device
|   |-- services/
|   |   |-- f5_client.py        # client iControl REST
|   |   `-- sync_service.py     # logic sync inventory
|   `-- requirements.txt
|-- frontend/
|   |-- index.html
|   `-- static/
|       |-- css/app.css
|       `-- js/
|           |-- app.js          # topology UI
|           `-- inventory.js    # devices dan inventory UI
|-- run.sh
`-- README.md
```

## Prasyarat

- Python 3.9 atau lebih baru.
- Akses jaringan dari server aplikasi ke management IP F5 BIG-IP port 443.
- User F5 yang memiliki akses iControl REST untuk membaca konfigurasi LTM.
- Untuk fitur action seperti enable/disable member atau clear connection, user F5 harus punya privilege yang sesuai.

## Setup

1. Install dependency backend.

```bash
cd backend
pip install -r requirements.txt
```

2. Buat `backend/.env` untuk secret enkripsi password device.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Lalu isi file `backend/.env`:

```env
SECRET_KEY=isi_dengan_key_yang_dihasilkan
```

Catatan: `SECRET_KEY` wajib ada untuk fitur Device Management karena password device akan dienkripsi sebelum disimpan.

## Menjalankan Aplikasi

### Linux / macOS / WSL

```bash
bash run.sh
```

### PowerShell / Windows

```powershell
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Buka aplikasi di browser:

```text
http://localhost:8000
```

## Cara Pakai

### Topology

1. Buka tab `Topology`.
2. Isi F5 host/IP, username, dan password.
3. Klik `Connect` untuk test koneksi dan memuat summary.
4. Masukkan keyword pencarian:
   - nama Virtual Server
   - nama Pool
   - destination IP, contoh `10.1.2.3`
   - IP dan port, contoh `10.1.2.3:443`
   - IP Pool Member
5. Klik `Search`.
6. Klik node VS, Pool, atau Member untuk melihat detail dan action yang tersedia.
7. Gunakan `Export PNG` atau `Export PDF` jika perlu menyimpan hasil.

### Devices

1. Buka tab `Devices`.
2. Klik `Add Device`.
3. Isi nama device, management IP, username, password, opsi SSL, dan status enabled.
4. Simpan device.
5. Gunakan `Test Connection` untuk validasi login.
6. Klik `Sync` per device atau `Sync All` untuk menarik inventory dari F5.

### Inventory

1. Buka tab `Inventory`.
2. Cari IP tertentu untuk melihat apakah IP tersebut muncul sebagai `VS`, `POOL_MEMBER`, atau `SELF_IP`.
3. Pilih device lalu klik `Load Inventory` untuk melihat semua inventory dari device tersebut.
4. Gunakan tombol clear hanya jika ingin menghapus data inventory lokal. Ini tidak menghapus konfigurasi di F5.

## Endpoint Penting

Topology:

- `GET /` - frontend aplikasi
- `POST /api/test-connection` - test koneksi F5 langsung
- `POST /api/health` - summary Virtual Server dan Pool
- `POST /api/search-unified?q=...` - search topologi
- `POST /api/vs-action` - enable/disable Virtual Server
- `POST /api/member-action` - enable/force-offline satu Pool Member
- `POST /api/member-action-bulk` - bulk action Pool Member
- `POST /api/pool-connections` - cek koneksi Pool
- `POST /api/clear-pool-connections` - clear koneksi server-side Pool

Devices dan inventory:

- `GET /devices` - list device
- `POST /devices` - tambah device
- `PUT /devices/{id}` - update device
- `DELETE /devices/{id}` - hapus device
- `POST /devices/{id}/test-connection` - test koneksi device tersimpan
- `POST /sync/device/{id}` - sync satu device
- `POST /sync/all` - sync semua device enabled
- `GET /inventory/search?ip=...` - search inventory IP
- `GET /inventory/all?device_id=...` - list inventory
- `DELETE /inventory/clear?device_id=...` - clear inventory lokal

## Catatan Operasional

- Backend berjalan di port `8000`.
- SSL verification default bernilai `false`, cocok untuk F5 dengan self-signed certificate. Aktifkan verify SSL jika certificate F5 valid dan trusted.
- Fitur topology manual memakai credential per request dan tidak menyimpan credential tersebut.
- Fitur Device Management menyimpan password terenkripsi di SQLite. Simpan `SECRET_KEY` dengan aman; jika key hilang, password lama tidak bisa didekripsi.
- Database lokal berada di `backend/inventory.db`.
- Jangan expose aplikasi ini ke internet publik tanpa autentikasi tambahan, segmentasi jaringan, dan kontrol akses yang jelas.
