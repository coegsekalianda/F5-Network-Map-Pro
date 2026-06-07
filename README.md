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
- Search inventory IP untuk tipe `Virtual Server`, `POOL_MEMBER`, dan `SELF_IP`.
- Export inventory ke XLSX untuk semua device atau device tertentu berdasarkan hostname.
- Monitoring realtime connection beberapa Virtual Server dari beberapa device F5 dalam satu dashboard.
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
|   |   |-- monitoring.py       # endpoint monitoring connection VS
|   |   `-- sync.py             # sync inventory device
|   |-- services/
|   |   |-- f5_client.py        # client iControl REST
|   |   |-- monitoring_service.py # logic polling stats VS
|   |   `-- sync_service.py     # logic sync inventory
|   `-- requirements.txt
|-- frontend/
|   |-- index.html
|   `-- static/
|       |-- css/app.css
|       `-- js/
|           |-- app.js          # topology UI
|           |-- inventory.js    # devices dan inventory UI
|           `-- monitoring.js   # monitoring connection UI
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

Monitoring juga bisa dibuka langsung melalui:

```text
http://localhost:8000/monitoring
```

## Cara Pakai

### Topology

1. Buka tab `Topology`.
2. Pilih device dari field connection. Jika device sudah tersimpan di `Devices`, host, username, password, dan opsi SSL akan dimuat otomatis lalu aplikasi langsung login.
3. Masukkan keyword pencarian:
   - nama Virtual Server
   - nama Pool
   - destination IP, contoh `10.1.2.3`
   - IP dan port, contoh `10.1.2.3:443`
   - IP Pool Member
4. Klik `Search`.
5. Klik node Virtual Server, Pool, atau Member untuk melihat detail dan action yang tersedia.
6. Gunakan `Export PNG` atau `Export PDF` jika perlu menyimpan hasil.

### Devices

1. Buka tab `Devices`.
2. Klik `Add Device`.
3. Isi nama device, management IP, username, password, opsi SSL, dan status enabled.
4. Simpan device.
5. Gunakan `Test Connection` untuk validasi login.
6. Klik `Sync` per device atau `Sync All` untuk menarik inventory dari F5.
7. Klik tombol `Monitoring` untuk membuka dashboard monitoring di URL `/monitoring`.

### Inventory

1. Buka tab `Inventory`.
2. Cari IP tertentu untuk melihat apakah IP tersebut muncul sebagai `Virtual Server`, `POOL_MEMBER`, atau `SELF_IP`.
3. Pada `Device Inventory`, ketik atau pilih hostname device lalu klik `Load Inventory`.
4. Pada `Export Inventory`, ketik atau pilih hostname device lalu klik `Export XLSX`.
5. Untuk export semua device, pilih `All Devices` pada field export. Field kosong akan menampilkan error `Pilih device terlebih dahulu`.
6. Gunakan tombol clear hanya jika ingin menghapus data inventory lokal. Ini tidak menghapus konfigurasi di F5.

### Monitoring

1. Buka URL `/monitoring` atau klik tombol `Monitoring` dari menu `Devices`.
2. Pada panel `VS Connection Monitor`, ketik atau pilih hostname device.
3. Klik `Load Virtual Server` untuk memuat daftar Virtual Server dari device tersebut.
4. Pilih Virtual Server, isi label custom jika perlu, lalu klik `Add VS Monitor`.
5. Dashboard akan polling koneksi setiap 1 detik tanpa reload halaman.
6. Gunakan `Combined chart` untuk membandingkan beberapa target dalam satu grafik.
7. Gunakan `Save Dashboard` dan `Load Dashboard` untuk menyimpan daftar target di localStorage browser.

Data realtime monitoring diambil langsung dari F5 iControl REST. Credential F5 tetap di backend; frontend hanya mengirim `device_id`, `partition`, dan `vs_name`.

## Endpoint Penting

Topology:

- `GET /` - frontend aplikasi
- `GET /monitoring` - frontend aplikasi langsung membuka halaman Monitoring
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
- `GET /inventory/export.xlsx?device_id=...` - export inventory XLSX; kosongkan `device_id` hanya untuk export semua device dari opsi `All Devices`
- `DELETE /inventory/clear?device_id=...` - clear inventory lokal

Monitoring:

- `GET /api/monitoring/virtual-servers?device_id=...` - list Virtual Server untuk pilihan monitoring
- `GET /api/monitoring/vs-connections?device_id=...&partition=Common&vs_name=...` - fetch stats connection satu Virtual Server
- `POST /api/monitoring/vs-connections/batch` - fetch stats connection beberapa Virtual Server sekaligus

## Catatan Operasional

- Backend berjalan di port `8000`.
- SSL verification default bernilai `false`, cocok untuk F5 dengan self-signed certificate. Aktifkan verify SSL jika certificate F5 valid dan trusted.
- Fitur Topology memakai credential dari device tersimpan dan langsung login saat device dipilih.
- Fitur Device Management menyimpan password terenkripsi di SQLite. Simpan `SECRET_KEY` dengan aman; jika key hilang, password lama tidak bisa didekripsi.
- Database lokal berada di `backend/inventory.db`.
- Jangan expose aplikasi ini ke internet publik tanpa autentikasi tambahan, segmentasi jaringan, dan kontrol akses yang jelas.
