# F5 Network Map Pro

Web app untuk visualisasi topologi F5 BIG-IP berdasarkan destination IP.

## Stack
- **Backend**: Python FastAPI (proxy ke iControl REST)
- **Frontend**: Vanilla HTML/CSS/JS dengan canvas rendering

## Cara Install

```bash
# 1. Clone / copy folder ini ke server
# 2. Pastikan Python 3.9+ terinstall

# Install dependencies
cd backend
pip install -r requirements.txt

# Jalankan
cd ..
bash run.sh
```

Buka browser: **http://localhost:8000**

## Cara Pakai

1. Isi **F5 Host**, **Username**, **Password** di sidebar
2. Klik **Connect** — akan test koneksi dan tampilkan summary
3. Masukkan **Destination IP** yang dicari
4. Klik **Search VS**
5. Topologi VS → Pool → Member akan muncul di canvas
6. Klik tiap node untuk detail
7. Export via tombol PNG / JSON / PDF

## Fitur

- Search VS by destination IP via iControl REST
- Visualisasi topologi interaktif (pan, zoom, klik)
- Health summary (total VS, up/down, pools)
- Detail panel per node (VS, pool, member)
- Export PNG, JSON, PDF

## Catatan

- Backend berjalan di port **8000**
- SSL verify dimatikan by default (F5 self-signed cert)
- Pastikan server ini bisa reach F5 management IP di port 443
- Credentials tidak disimpan, hanya dipakai per-request

## Struktur

```
f5-topology/
├── backend/
│   ├── main.py          # FastAPI app
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   └── static/
│       ├── css/app.css
│       └── js/
│           └── app.js        # App logic
└── run.sh
```
