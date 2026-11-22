# disk-sentinel-free

💾 **HDD Sentinel Free** — CLI Disk Health Monitor gratis, ringan, dan open source.  
Alternatif CLI dari HDD Sentinel untuk memantau kesehatan harddisk dan SSD melalui command line.

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-green.svg)](https://www.microsoft.com/windows)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)](https://www.linux.org)

---

## 📌 Tentang Project Ini

Project ini adalah alat monitoring kesehatan disk berbasis CLI yang ditulis dengan Python. Cocok untuk pengguna yang ingin mengecek kesehatan disk (health %, suhu, bad sector, jam menyala, used/free space) tanpa perlu install aplikasi berukuran besar.

Dibuat sebagai alternatif **gratis dan ringan** dari [HDD Sentinel](https://www.hddsentinel.com/).

## ✨ Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| 📋 Daftar Disk | Lihat semua disk fisik dengan model, kapasitas, tipe, suhu, dan health |
| 💾 Used / Free Space | Lihat ruang terpakai dan tersisa per disk |
| ❤️ Health Score | Skor kesehatan 0–100 berdasarkan atribut SMART kritis |
| 🌡️ Suhu & Jam Menyala | Monitoring suhu disk dan total jam disk menyala (Power-On Hours) |
| ⚠️ Bad Sector Deteksi | Deteksi reallocated sector, pending sector, offline uncorrectable |
| 💿 SSD Wear | Estimasi usia SSD berdasarkan wear leveling |
| 🖥️ Mode Interaktif | Menu interaktif untuk pengguna yang tidak hafal perintah CLI |
| ⏱️ Monitor Real-time | Refresh otomatis tiap beberapa detik (mirip watch) |
| 📤 Export Laporan | Simpan hasil ke file JSON atau CSV |
| 🌀 Loading Spinner | Animasi loading saat proses berjalan (mirip pnpm/bun) |
| 🪟 Windows First | Dikonfigursi optimal untuk Windows (PowerShell), juga support Linux |

## 🚀 Instalasi

### 1. Clone Repository

```bash
git clone https://github.com/RidhoPutraBramantyo/disk-sentinel-free.git
cd disk-sentinel-free
```

### 2. Install Dependency (Opsional tapi Disarankan)

```bash
pip install -r requirements.txt
```

> Jika tidak install dependency, program tetap berjalan dengan library bawaan Python, hanya tampilan akan tanpa warna.

### 3. Jalankan

```bash
python hdd_sentinel_free.py
```

---

## 📖 Panduan Penggunaan

### Mode Interaktif (Direkomendasikan untuk Pemula)

Jalankan tanpa argumen untuk membuka menu interaktif:

```bash
python hdd_sentinel_free.py
```

Kamu akan melihat menu:
```
=== HDD Sentinel Free - Menu Interaktif ===

1. Lihat semua disk (list)
2. Ringkasan kesehatan (health)
3. Detail disk (info)
4. Lihat atribut SMART (smart)
5. Monitor real-time (monitor)
6. Export laporan (export)
7. Keluar

Pilih menu [1-7]:
```

Jika memilih opsi yang membutuhkan disk (misal `info` atau `smart`), program akan otomatis menampilkan daftar disk untuk dipilih:

```
Pilih disk untuk detail:
  1. PhysicalDrive0 - Micron MTFDKCD512QGN... (476.94 GB) [SSD]
  2. PhysicalDrive1 - ADATA LEGEND 710 (476.94 GB) [SSD]
  0. Kembali

Pilih [1-2] atau 0:
```

### Mode CLI (Perintah Langsung)

#### 1. Daftar semua disk

```bash
python hdd_sentinel_free.py list
```

Contoh output:
```
DEVICE        MODEL                     TYPE    SIZE       USED       FREE       TEMP   HEALTH   STATUS
--------------------------------------------------------------------------------------------------------------
1             ADATA LEGEND 710          SSD     476.94 GB  317.39 GB  159.53 GB  N/A    80% OK
0             Micron MTFDKCD512QGN...   SSD     476.94 GB  431.00 GB  45.67 GB   N/A    80% OK
```

#### 2. Ringkasan kesehatan

```bash
python hdd_sentinel_free.py health
```

#### 3. Detail satu disk

```bash
# Windows
python hdd_sentinel_free.py info PhysicalDrive0

# Linux
python hdd_sentinel_free.py info /dev/sda
```

Jika tidak menentukan nama disk, program akan meminta pilihan secara interaktif.

#### 4. Lihat atribut SMART mentah

```bash
python hdd_sentinel_free.py smart PhysicalDrive0
```

#### 5. Monitor real-time

```bash
python hdd_sentinel_free.py monitor -i 5
```
*(Refresh setiap 5 detik. Tekan Ctrl+C untuk berhenti.)*

#### 6. Export laporan

```bash
python hdd_sentinel_free.py export -f json -o laporan_disk.json
python hdd_sentinel_free.py export -f csv -o laporan_disk.csv
```

---

## 🛠️ Persyaratan Sistem

- **Python 3.8+**
- **Windows** (PowerShell bawaan, direkomendasikan), **Linux**, atau macOS
- (Opsional) [smartmontools](https://www.smartmontools.org/) untuk data SMART paling akurat di Windows

### Dependency Python

| Paket | Fungsi |
|-------|--------|
| `colorama` | Warna output di terminal |
| `psutil` | Data used/free space (Linux) |

Install semua sekaligus:
```bash
pip install -r requirements.txt
```

---

## ⚠️ Catatan Penting

### Untuk Windows
Program menggunakan **PowerShell `Get-PhysicalDisk`** bawaan Windows. Jika `smartctl` (dari smartmontools) terinstal, program akan otomatis menggunakannya untuk data SMART yang lebih detail (suhu, bad sector, dll.).

Tanpa `smartctl`, health score dibatasi maksimal 80% karena data atribut SMART detail tidak tersedia.

### Data & Privasi
Semua data (model disk, serial number, suhu, health, used/free) diambil **secara lokal** pada komputer kamu dan **tidak pernah dikirim ke server mana pun**. Output hanya ditampilkan di CLI atau disimpan ke file JSON/CSV lokal.

### Merek Dagang
"HDD Sentinel" adalah merek dagang terdaftar. Project ini adalah alternatif independen dan gratis, bukan produk resmi dari HDD Sentinel.

---

## 🤝 Kontribusi

Kontribusi sangat diterima! Berikut cara berkontribusi:

1. Fork repository ini
2. Buat branch baru: `git checkout -b fitur-nama-fitur`
3. Commit perubahan: `git commit -m "Tambah fitur ..."`
4. Push ke branch: `git push origin fitur-nama-fitur`
5. Buat Pull Request

### Ide Kontribusi
- Tambah dukungan macOS
- Tambah notifikasi email/desktop saat health menurun
- Tambah jadwal otomatis (cron/task scheduler)
- Tambah unit test
- Terjemahkan README ke bahasa Inggris

---

## 📄 Lisensi

Distribusi di bawah [MIT License](LICENSE). Bebas digunakan, dimodifikasi, dan didistribusikan ulang.

---

## 👤 Author

**Ridho Putra Bramantyo**  
GitHub: [@RidhoPutraBramantyo](https://github.com/RidhoPutraBramantyo)

Jika bermanfaat, silakan give ⭐ pada repository ini!
