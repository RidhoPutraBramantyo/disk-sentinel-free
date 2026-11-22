#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HDD Sentinel Free - CLI Disk Health Monitor (Open Source / Gratis)
Mirip HDD Sentinel: menampilkan kesehatan disk, suhu, SMART, jam menyala,
bad sector, dan estimasi usia SSD.

Platform: Windows (utama), Linux, macOS
Author: Open Source
"""

import argparse
import csv
import itertools
import json
import os
import platform
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
try:
    import colorama
    from colorama import Fore, Style

    colorama.init(autoreset=True)
    HAS_COLORAMA = True
except ImportError:  # pragma: no cover - fallback saat colorama belum terpasang
    HAS_COLORAMA = False

    class _Dummy:
        def __getattr__(self, name):
            return ""

    Fore = Style = _Dummy()

try:
    import psutil  # type: ignore

    HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def c(text, color):
    """Wrap text with color if colorama is available."""
    if HAS_COLORAMA:
        return f"{color}{text}{Style.RESET_ALL}"
    return text


RED = Fore.RED if HAS_COLORAMA else ""
GREEN = Fore.GREEN if HAS_COLORAMA else ""
YELLOW = Fore.YELLOW if HAS_COLORAMA else ""
CYAN = Fore.CYAN if HAS_COLORAMA else ""
MAGENTA = Fore.MAGENTA if HAS_COLORAMA else ""
WHITE = Fore.WHITE if HAS_COLORAMA else ""
BOLD = Style.BRIGHT if HAS_COLORAMA else ""
RESET = Style.RESET_ALL if HAS_COLORAMA else ""


# ---------------------------------------------------------------------------
# Loading spinner (mirip pnpm / bun)
# ---------------------------------------------------------------------------
def is_tty():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class Spinner:
    """Loading spinner yang berjalan di thread terpisah."""

    def __init__(self, message="Memproses", delay=0.08):
        self.message = message
        self.delay = delay
        self._running = False
        self._thread = None
        # Karakter braille (halus) untuk terminal modern
        self._unicode_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠶", "⠷", "⠾", "⠿"]
        # Fallback aman untuk Windows CMD lama
        self._fallback_chars = ["-", "\\", "|", "/"]

    def _supports_unicode(self):
        try:
            return sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") in (
                "utf8", "utf-8",
            )
        except Exception:
            return False

    def _spin(self):
        chars = self._unicode_chars if self._supports_unicode() else self._fallback_chars
        color = CYAN if HAS_COLORAMA else ""
        for char in itertools.cycle(chars):
            if not self._running:
                break
            line = f"\r{color}{char}{RESET} {self.message}..."
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(self.delay)
        # Bersihkan baris spinner setelah berhenti
        sys.stdout.write("\r" + " " * (len(self.message) + 6) + "\r")
        sys.stdout.flush()

    def start(self):
        if not is_tty():
            return
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class DiskInfo:
    def __init__(
        self,
        device,
        model="",
        serial="",
        size=0,
        interface="",
        type_="",
        status="Unknown",
    ):
        self.device = device
        self.model = model
        self.serial = serial
        self.size = size
        self.interface = interface
        self.type = type_
        self.status = status
        self.temperature = None
        self.health = 100
        self.power_on_hours = 0
        self.smart_attrs = {}
        self.smart_status = "Unknown"
        self.smart_available = False
        self.used_bytes = 0
        self.free_bytes = 0

    def to_dict(self):
        return {
            "device": self.device,
            "model": self.model,
            "serial": self.serial,
            "size_bytes": self.size,
            "size_human": format_size(self.size),
            "used_bytes": self.used_bytes,
            "used_human": format_size(self.used_bytes),
            "free_bytes": self.free_bytes,
            "free_human": format_size(self.free_bytes),
            "interface": self.interface,
            "type": self.type,
            "status": self.status,
            "temperature_c": self.temperature,
            "health_percent": self.health,
            "power_on_hours": self.power_on_hours,
            "smart_status": self.smart_status,
            "smart_available": self.smart_available,
            "smart_attrs": self.smart_attrs,
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def run_cmd(cmd, timeout=20, shell=False):
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return -1, "", str(exc)


def smartctl_available():
    rc, _, _ = run_cmd(["smartctl", "--version"], timeout=10)
    return rc == 0


def parse_size(raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def format_size(bytes_):
    if not bytes_:
        return "N/A"
    size = float(bytes_)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def format_temp(t):
    if t is None:
        return "N/A"
    return f"{t}\u00b0C"


def format_health(h):
    if h is None:
        return c("N/A", YELLOW)
    if h >= 90:
        return c(f"{h}%", GREEN)
    elif h >= 70:
        return c(f"{h}%", YELLOW)
    elif h >= 40:
        return c(f"{h}%", YELLOW)
    else:
        return c(f"{h}%", RED)


def health_color(h):
    if h is None:
        return YELLOW
    if h >= 90:
        return GREEN
    elif h >= 70:
        return YELLOW
    else:
        return RED


def clear_screen():
    os.system("cls" if platform.system() == "Windows" else "clear")


# ---------------------------------------------------------------------------
# Disk usage (used / free space)
# ---------------------------------------------------------------------------
def get_disk_usage_windows(device):
    """Map PhysicalDriveN ke volume logis dan hitung used/free via PowerShell."""
    try:
        num = "".join(filter(str.isdigit, device))
        if not num:
            return None, None
        ps = (
            f"Get-Disk -Number {num} | Get-Partition | Get-Volume | "
            f"Select-Object DriveLetter, Size, SizeRemaining, FileSystemLabel | "
            f"ConvertTo-Json -Depth 2"
        )
        rc, out, _ = run_cmd(["powershell", "-Command", ps], shell=True)
        if rc != 0 or not out.strip():
            return None, None
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        total_size = 0
        total_remaining = 0
        for vol in data:
            total_size += parse_size(vol.get("Size", 0))
            total_remaining += parse_size(vol.get("SizeRemaining", 0))
        if total_size > 0:
            used = max(0, total_size - total_remaining)
            free = max(0, total_remaining)
            return used, free
    except Exception:
        pass
    return None, None


def get_disk_usage_linux(device):
    """Cari mount point di bawah /dev/{device}* dan hitung used/free."""
    try:
        if not HAS_PSUTIL:
            return None, None
        used_total = 0
        free_total = 0
        found = False
        for part in psutil.disk_partitions(all=False):
            if part.device.startswith(f"/dev/{device}"):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    used_total += usage.used
                    free_total += usage.free
                    found = True
                except (OSError, PermissionError):
                    continue
        if found:
            return used_total, free_total
    except Exception:
        pass
    return None, None


def get_disk_usage(disk):
    system = platform.system()
    if system == "Windows":
        return get_disk_usage_windows(disk.device)
    if system == "Linux":
        return get_disk_usage_linux(disk.device)
    return None, None


# ---------------------------------------------------------------------------
# Disk discovery
# ---------------------------------------------------------------------------
def discover_windows_disks():
    """Detect physical disk di Windows via PowerShell Get-PhysicalDisk."""
    disks = []
    ps = (
        "Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,SerialNumber,"
        "Size,MediaType,BusType,HealthStatus,OperationalStatus | "
        "ConvertTo-Json -Depth 3"
    )
    rc, out, _ = run_cmd(["powershell", "-Command", ps], shell=True)
    if rc != 0 or not out.strip():
        return disks

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return disks

    if isinstance(data, dict):
        data = [data]

    for item in data:
        if not isinstance(item, dict):
            continue
        device = str(item.get("DeviceId", "")).strip().replace("\\\\.\\", "")
        if not device:
            continue
        model = item.get("FriendlyName", "") or ""
        serial = item.get("SerialNumber", "") or ""
        size = parse_size(item.get("Size", 0))
        mediatype = item.get("MediaType", "") or ""
        iface = item.get("BusType", "") or ""
        health = item.get("HealthStatus", "") or "Unknown"
        op_status = item.get("OperationalStatus", [])
        if isinstance(op_status, list):
            status = ", ".join(str(s) for s in op_status) or health
        else:
            status = str(op_status) if op_status else health
        disks.append(
            DiskInfo(
                device=device,
                model=model,
                serial=serial,
                size=size,
                interface=iface,
                type_=mediatype,
                status=status or "Unknown",
            )
        )
    return disks


def discover_linux_disks():
    """Detect block device di Linux via /sys/class/block."""
    disks = []
    sys_block = "/sys/class/block"
    if not os.path.isdir(sys_block):
        return disks

    for entry in sorted(os.listdir(sys_block)):
        path = os.path.join(sys_block, entry)
        if not os.path.isdir(path):
            continue
        # Skip virtual/loop devices
        if entry.startswith(("loop", "ram", "fd")):
            continue

        size = 0
        size_file = os.path.join(path, "size")
        if os.path.exists(size_file):
            try:
                with open(size_file, "r") as f:
                    sectors = int(f.read().strip())
                    size = sectors * 512
            except (OSError, ValueError):
                pass

        model = ""
        model_file = os.path.join(path, "device", "model")
        if os.path.exists(model_file):
            try:
                with open(model_file, "r") as f:
                    model = f.read().strip()
            except OSError:
                pass

        disks.append(
            DiskInfo(
                device=entry,
                model=model,
                size=size,
            )
        )
    return disks


def discover_disks():
    """Deteksi semua disk fisik dengan loading spinner."""
    spinner = Spinner("Mendeteksi disk fisik")
    spinner.start()
    try:
        system = platform.system()
        if system == "Windows":
            disks = discover_windows_disks()
        elif system == "Linux":
            disks = discover_linux_disks()
        else:
            disks = []
        return disks
    finally:
        spinner.stop()


# ---------------------------------------------------------------------------
# SMART gathering
# ---------------------------------------------------------------------------
def get_smart_smartctl(device):
    """Ambil data SMART via smartctl (cross-platform)."""
    info = {"smart_available": False, "attrs": {}}
    target = device
    if platform.system() == "Windows" and not target.startswith("\\\\.\\"):
        target = "\\\\.\\" + target

    rc, out, _ = run_cmd(["smartctl", "-a", target], timeout=20)
    # smartctl exit 2 = SMART info tersedia tapi ada error kecil, tetap bisa di-parse
    if rc not in (0, 2) and not out:
        return info

    info["smart_available"] = True
    for line in out.splitlines():
        if "Device Model:" in line:
            info["model"] = line.split(":", 1)[1].strip()
        elif "Serial Number:" in line:
            info["serial"] = line.split(":", 1)[1].strip()
        elif "SMART overall-health self-assessment test result:" in line:
            info["smart_status"] = line.split(":", 1)[1].strip()
        elif "LU WWN Device Id:" in line:
            info["wwn"] = line.split(":", 1)[1].strip()

    in_attrs = False
    for line in out.splitlines():
        if "ID# ATTRIBUTE_NAME" in line:
            in_attrs = True
            continue
        if in_attrs and line.strip():
            parts = line.split(None, 10)
            if len(parts) >= 10 and parts[0].isdigit():
                attr_id = int(parts[0])
                info.setdefault("attrs", {})[attr_id] = {
                    "name": parts[1],
                    "flag": parts[2],
                    "value": parts[3],
                    "worst": parts[4],
                    "thresh": parts[5],
                    "type": parts[6],
                    "updated": parts[7],
                    "when_failed": parts[8],
                    "raw": parts[9] if len(parts) > 9 else "",
                }
    return info


def get_smart_windows_powershell(device):
    """Fallback Windows via PowerShell Get-PhysicalDisk jika smartctl tidak ada."""
    ps = (
        "Get-PhysicalDisk | Where-Object { $_.DeviceID -eq '%s' } | "
        "Select-Object DeviceID,FriendlyName,SerialNumber,HealthStatus,"
        "OperationalStatus,Size,MediaType | ConvertTo-Json -Depth 3"
    ) % device
    rc, out, _ = run_cmd(["powershell", "-Command", ps], shell=True)
    info = {"smart_available": False, "attrs": {}}
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            if isinstance(data, list):
                data = data[0] if data else {}
            info["model"] = data.get("FriendlyName", "")
            info["serial"] = data.get("SerialNumber", "")
            info["smart_status"] = data.get("HealthStatus", "Unknown")
            info["size"] = data.get("Size", 0)
            info["media_type"] = data.get("MediaType", "")
            info["operational_status"] = data.get("OperationalStatus", [])
        except (json.JSONDecodeError, IndexError):
            pass
    return info


def get_smart(disk):
    system = platform.system()
    if system == "Windows":
        if smartctl_available():
            return get_smart_smartctl(disk.device)
        return get_smart_windows_powershell(disk.device)
    if system == "Linux" and smartctl_available():
        return get_smart_smartctl(f"/dev/{disk.device}")
    return {"smart_available": False, "attrs": {}}


# ---------------------------------------------------------------------------
# SMART attribute parsing
# ---------------------------------------------------------------------------
def _raw_int(attr):
    raw = str(attr.get("raw", ""))
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        try:
            return int(raw)
        except ValueError:
            return 0


def get_temperature(smart):
    attrs = smart.get("attrs", {})
    for aid in (194, 190):  # Temperature / Airflow Temperature
        if aid in attrs:
            return _raw_int(attrs[aid])
    return None


def get_power_on_hours(smart):
    attrs = smart.get("attrs", {})
    if 9 in attrs:  # Power-On Hours
        return _raw_int(attrs[9])
    return 0


def compute_health(disk, smart):
    """Hitung health score 0-100 berdasarkan atribut SMART kritis."""
    health = 100
    reasons = []

    if not smart.get("smart_available"):
        health = min(health, 80)
        reasons.append("SMART tidak tersedia")

    status = str(smart.get("smart_status", "")).lower()
    good_status = ("ok", "pass", "healthy", "good")
    if "failed" in status or "unhealthy" in status or "bad" in status:
        health = min(health, 10)
        reasons.append("SMART status: GAGAL")
    elif status not in ("", "unknown") and not any(g in status for g in good_status):
        health = min(health, 50)
        reasons.append(f"SMART status: {status}")

    attrs = smart.get("attrs", {})

    # Kritis: bad sector / pending / offline uncorrectable
    critical = {
        5: "Reallocated Sectors",
        197: "Current Pending Sector",
        198: "Offline Uncorrectable",
        196: "Reallocation Event Count",
    }
    for aid, label in critical.items():
        if aid in attrs:
            raw = _raw_int(attrs[aid])
            if raw > 0:
                health = min(health, 25)
                reasons.append(f"{label}: {raw}")

    # Suhu
    temp = get_temperature(smart)
    if temp is not None:
        disk.temperature = temp
        if temp >= 60:
            health = min(health, 50)
            reasons.append(f"Suhu tinggi: {temp}\u00b0C")
        elif temp >= 50:
            health = min(health, 75)

    # Wear SSD
    for aid in (231, 233):  # SSD Life Left / Media Wearout Indicator
        if aid in attrs:
            try:
                val = int(attrs[aid].get("value", 100))
            except ValueError:
                val = 100
            if val < 20:
                health = min(health, 35)
                reasons.append(f"Usia SSD menipis (attr {aid})")

    return max(0, health), reasons


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
def enrich_disk(disk):
    smart = get_smart(disk)
    disk.smart_available = smart.get("smart_available", False)
    disk.smart_attrs = smart.get("attrs", {})
    disk.smart_status = smart.get("smart_status", "Unknown")
    if smart.get("model"):
        disk.model = smart["model"] or disk.model
    if smart.get("serial"):
        disk.serial = smart["serial"] or disk.serial
    if smart.get("size") and not disk.size:
        disk.size = smart["size"]
    disk.temperature = get_temperature(smart)
    disk.power_on_hours = get_power_on_hours(smart)
    disk.health, _ = compute_health(disk, smart)

    # Disk usage (used / free)
    used, free = get_disk_usage(disk)
    if used is not None:
        disk.used_bytes = used
    if free is not None:
        disk.free_bytes = free
    return disk


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def pick_disk_interactive(disks, prompt="Pilih disk"):
    """Tampilkan daftar disk dan biarkan user pilih satu. Kembalikan DiskInfo atau None."""
    if not disks:
        print(c("Tidak ada disk tersedia.", YELLOW))
        return None
    print(f"\n{BOLD}{prompt}{RESET}")
    for i, d in enumerate(disks, 1):
        model = (d.model[:40] + "..") if len(d.model) > 40 else d.model
        print(f"  {i}. {d.device} - {model} ({format_size(d.size)}) [{d.type or 'N/A'}]")
    print("  0. Kembali")
    try:
        choice = input(f"\nPilih [1-{len(disks)}] atau 0: ").strip()
        if choice == "0":
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(disks):
            return disks[idx]
    except (ValueError, EOFError, KeyboardInterrupt):
        pass
    print(c("Pilihan tidak valid.", RED))
    return None


def press_enter_to_continue():
    try:
        input("\nTekan Enter untuk kembali ke menu...")
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_list(args):
    disks = discover_disks()
    if not disks:
        print(c("Tidak ada disk fisik terdeteksi.", YELLOW))
        return 0

    # Enrich semua disk agar used/free tersedia
    for d in disks:
        enrich_disk(d)

    print(
        f"\n{BOLD}{'DEVICE':<14}{'MODEL':<26}{'TYPE':<8}{'SIZE':<11}"
        f"{'USED':<11}{'FREE':<11}{'TEMP':<7}{'HEALTH':<9} STATUS{RESET}"
    )
    print("-" * 110)
    for d in disks:
        dev = d.device
        model = (d.model[:24] + "..") if len(d.model) > 24 else d.model
        dtype = d.type or "N/A"
        size = format_size(d.size)
        used = format_size(d.used_bytes) if d.used_bytes else "N/A"
        free = format_size(d.free_bytes) if d.free_bytes else "N/A"
        temp = format_temp(d.temperature)
        health = format_health(d.health)
        status = d.status
        print(
            f"{dev:<14}{model:<26}{dtype:<8}{size:<11}"
            f"{used:<11}{free:<11}{temp:<7}{health:<9} {status}"
        )
    print()
    return 0


def cmd_info(args):
    disks = discover_disks()
    target = getattr(args, "device", None)
    disk = None
    if target:
        disk = next(
            (d for d in disks if d.device == target or d.device.endswith(target)),
            None,
        )
    if not disk:
        if target:
            print(c(f"Disk '{target}' tidak ditemukan.", RED))
        print("Disk tersedia: " + ", ".join(d.device for d in disks))
        return 1

    enrich_disk(disk)
    print(f"\n{BOLD}=== INFORMASI DISK: {disk.device} ==={RESET}")
    print(f"Model            : {disk.model or 'N/A'}")
    print(f"Serial           : {disk.serial or 'N/A'}")
    print(f"Tipe             : {disk.type or 'N/A'}")
    print(f"Antarmuka        : {disk.interface or 'N/A'}")
    print(f"Kapasitas        : {format_size(disk.size)}")
    if disk.used_bytes or disk.free_bytes:
        pct = (disk.used_bytes / disk.size * 100) if disk.size else 0
        print(f"Terpakai         : {format_size(disk.used_bytes)} ({pct:.1f}%)")
        print(f"Tersisa          : {format_size(disk.free_bytes)}")
    print(f"Status           : {disk.status}")
    print(f"SMART tersedia   : {'Ya' if disk.smart_available else 'Tidak'}")
    print(f"SMART status     : {disk.smart_status}")
    print(f"Suhu             : {format_temp(disk.temperature)}")
    print(f"Jam menyala      : {disk.power_on_hours} jam")
    print(f"Kesehatan        : {format_health(disk.health)}")
    print()

    # Show key SMART attributes
    attrs = disk.smart_attrs
    important = [1, 5, 7, 9, 190, 193, 194, 196, 197, 198, 231, 233, 241, 242]
    shown = [aid for aid in important if aid in attrs]
    if shown:
        print(f"{BOLD}Atribut SMART penting:{RESET}")
        print(
            f"{'ID':<5}{'NAMA':<26}{'VALUE':<9}{'WORST':<9}{'THRESH':<9}{'RAW'}"
        )
        print("-" * 70)
        for aid in shown:
            a = attrs[aid]
            print(
                f"{aid:<5}{a.get('name',''):<26}{a.get('value',''):<9}"
                f"{a.get('worst',''):<9}{a.get('thresh',''):<9}{a.get('raw','')}"
            )
    print()
    return 0


def cmd_smart(args):
    """Tampilkan seluruh atribut SMART mentah."""
    disks = discover_disks()
    target = getattr(args, "device", None)
    disk = None
    if target:
        disk = next(
            (d for d in disks if d.device == target or d.device.endswith(target)),
            None,
        )
    if not disk:
        if target:
            print(c(f"Disk '{target}' tidak ditemukan.", RED))
        print("Disk tersedia: " + ", ".join(d.device for d in disks))
        return 1

    enrich_disk(disk)
    attrs = disk.smart_attrs
    if not attrs:
        print(c("Tidak ada data atribut SMART untuk disk ini.", YELLOW))
        return 0

    print(f"\n{BOLD}SMART ATTRIBUTES - {disk.device}{RESET}")
    print(
        f"{'ID':<5}{'NAMA':<30}{'FLAG':<8}{'VALUE':<9}{'WORST':<9}"
        f"{'THRESH':<9}{'TYPE':<10}{'UPDATED':<10}{'WHEN_FAILED':<14}{'RAW'}"
    )
    print("-" * 120)
    for aid in sorted(attrs.keys()):
        a = attrs[aid]
        print(
            f"{aid:<5}{a.get('name',''):<30}{a.get('flag',''):<8}"
            f"{a.get('value',''):<9}{a.get('worst',''):<9}"
            f"{a.get('thresh',''):<9}{a.get('type',''):<10}"
            f"{a.get('updated',''):<10}{a.get('when_failed',''):<14}{a.get('raw','')}"
        )
    print()
    return 0


def cmd_health(args):
    """Ringkasan kesehatan semua disk."""
    disks = discover_disks()
    if not disks:
        print(c("Tidak ada disk fisik terdeteksi.", YELLOW))
        return 0

    print(f"\n{BOLD}RINGKASAN KESEHATAN DISK{RESET}")
    print(f"{'DEVICE':<14}{'MODEL':<26}{'HEALTH':<9} TEMP   STATUS")
    print("-" * 72)
    any_bad = False
    for d in disks:
        enrich_disk(d)
        dev = d.device
        model = (d.model[:24] + "..") if len(d.model) > 24 else d.model
        health = d.health
        temp = format_temp(d.temperature)
        status = d.status
        col = health_color(health)
        print(
            f"{dev:<14}{model:<26}{c(f'{health}%', col):<9} {temp:<7}{status}"
        )
        if health < 70:
            any_bad = True
    print()
    if any_bad:
        print(c("PERINGATAN: Ada disk dengan kesehatan di bawah 70%! Segera cadangkan data.", RED))
    else:
        print(c("Semua disk dalam kondisi baik (>= 70%).", GREEN))
    return 0


def cmd_monitor(args):
    """Monitor real-time (mirip watch)."""
    interval = args.interval
    print(c("Tekan Ctrl+C untuk berhenti.", CYAN))
    try:
        while True:
            clear_screen()
            print(f"{BOLD}HDD Sentinel Free - Monitor Real-time{RESET}")
            print(f"Refresh setiap {interval} detik | Tekan Ctrl+C untuk keluar\n")
            cmd_list(type("Args", (), {"device": None})())
            time.sleep(interval)
    except KeyboardInterrupt:
        print(c("\nMonitor dihentikan.", CYAN))
    return 0


def cmd_export(args):
    """Export hasil ke JSON atau CSV."""
    spinner = Spinner("Mengumpulkan dan menuliskan laporan")
    spinner.start()
    try:
        disks = discover_disks()
        if not disks:
            print(c("Tidak ada disk fisik terdeteksi.", YELLOW))
            return 0

        data = [enrich_disk(d).to_dict() for d in disks]
        fmt = getattr(args, "format", "json").lower()
        out_file = getattr(args, "output", None) or f"hdd_sentinel_report.{fmt}"

        try:
            if fmt == "json":
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            elif fmt == "csv":
                with open(out_file, "w", encoding="utf-8", newline="") as f:
                    if data:
                        writer = csv.DictWriter(f, fieldnames=data[0].keys())
                        writer.writeheader()
                        for row in data:
                            row_copy = dict(row)
                            row_copy["smart_attrs"] = json.dumps(
                                row_copy.get("smart_attrs", {}), ensure_ascii=False
                            )
                            writer.writerow(row_copy)
            else:
                print(c("Format tidak didukung. Gunakan json atau csv.", RED))
                return 1
            print(c(f"Hasil diexport ke: {out_file}", GREEN))
        except OSError as exc:
            print(c(f"Gagal menuliskan file: {exc}", RED))
            return 1
    finally:
        spinner.stop()
    return 0


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------
def cmd_interactive(args):
    """Menu interaktif untuk pengguna yang tidak ingin hafal CLI."""
    while True:
        clear_screen()
        print(f"{BOLD}=== HDD Sentinel Free - Menu Interaktif ==={RESET}\n")
        print("1. Lihat semua disk (list)")
        print("2. Ringkasan kesehatan (health)")
        print("3. Detail disk (info)")
        print("4. Lihat atribut SMART (smart)")
        print("5. Monitor real-time (monitor)")
        print("6. Export laporan (export)")
        print("7. Keluar")
        print()
        try:
            choice = input("Pilih menu [1-7]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(c("\nKeluar.", CYAN))
            return 0

        if choice == "1":
            spinner = Spinner("Memuat daftar disk")
            spinner.start()
            cmd_list(args)
            spinner.stop()
            press_enter_to_continue()
        elif choice == "2":
            spinner = Spinner("Menghitung kesehatan disk")
            spinner.start()
            cmd_health(args)
            spinner.stop()
            press_enter_to_continue()
        elif choice == "3":
            disks = discover_disks()
            disk = pick_disk_interactive(disks, "Pilih disk untuk detail:")
            if disk:
                old = getattr(args, "device", None)
                args.device = disk.device
                spinner = Spinner("Memuat detail disk")
                spinner.start()
                cmd_info(args)
                spinner.stop()
                args.device = old
                press_enter_to_continue()
        elif choice == "4":
            disks = discover_disks()
            disk = pick_disk_interactive(disks, "Pilih disk untuk SMART:")
            if disk:
                old = getattr(args, "device", None)
                args.device = disk.device
                spinner = Spinner("Memuat atribut SMART")
                spinner.start()
                cmd_smart(args)
                spinner.stop()
                args.device = old
                press_enter_to_continue()
        elif choice == "5":
            try:
                interval = input("Interval refresh dalam detik [default 5]: ").strip()
                args.interval = int(interval) if interval else 5
            except ValueError:
                args.interval = 5
            cmd_monitor(args)
        elif choice == "6":
            fmt_choice = input("Format output [1=JSON / 2=CSV, default JSON]: ").strip()
            args.format = "csv" if fmt_choice == "2" else "json"
            out = input("Nama file output (kosongkan untuk default): ").strip()
            args.output = out if out else None
            spinner = Spinner("Menyiapkan export laporan")
            spinner.start()
            cmd_export(args)
            spinner.stop()
            press_enter_to_continue()
        elif choice == "7":
            print(c("Sampai jumpanya!", CYAN))
            return 0
        else:
            print(c("Pilihan tidak valid.", RED))
            time.sleep(1)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="hdd_sentinel_free.py",
        description="HDD Sentinel Free - CLI Disk Health Monitor (Gratis/Open Source)",
        epilog="Contoh: python hdd_sentinel_free.py list\n"
               "         python hdd_sentinel_free.py info PhysicalDrive0\n"
               "         python hdd_sentinel_free.py smart /dev/sda\n"
               "         python hdd_sentinel_free.py monitor -i 5\n"
               "         python hdd_sentinel_free.py export -f json -o laporan.json\n"
               "         python hdd_sentinel_free.py              (mode interaktif)",
    )
    sub = parser.add_subparsers(dest="command", help="Perintah yang tersedia")

    sub.add_parser("list", help="Daftar semua disk dengan ringkasan kesehatan")
    sub.add_parser("health", help="Ringkasan kesehatan semua disk")

    p_info = sub.add_parser("info", help="Informasi detail satu disk")
    p_info.add_argument("device", nargs="?", default=None,
                        help="Contoh: PhysicalDrive0 atau /dev/sda (kosongkan untuk mode interaktif)")

    p_smart = sub.add_parser("smart", help="Tampilkan atribut SMART mentah")
    p_smart.add_argument("device", nargs="?", default=None,
                         help="Contoh: PhysicalDrive0 atau /dev/sda (kosongkan untuk mode interaktif)")

    p_mon = sub.add_parser("monitor", help="Monitor real-time disk")
    p_mon.add_argument("-i", "--interval", type=int, default=5,
                       help="Interval refresh dalam detik (default: 5)")

    p_exp = sub.add_parser("export", help="Export laporan ke JSON/CSV")
    p_exp.add_argument("-f", "--format", choices=["json", "csv"], default="json",
                       help="Format output (default: json)")
    p_exp.add_argument("-o", "--output", help="Nama file output")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # Startup spinner saat script pertama kali dijalankan
    startup = Spinner("Memuat HDD Sentinel Free")
    startup.start()
    time.sleep(0.6)
    startup.stop()

    # Tidak ada subcommand sama sekali -> buka menu interaktif
    if not args.command:
        return cmd_interactive(args)

    # Jika perintah membutuhkan device tapi tidak diberikan -> masuk pemilihan interaktif
    if args.command in ("info", "smart") and not getattr(args, "device", None):
        disks = discover_disks()
        disk = pick_disk_interactive(disks, f"Pilih disk untuk {args.command}:")
        if disk:
            args.device = disk.device
        else:
            return 0

    commands = {
        "list": cmd_list,
        "health": cmd_health,
        "info": cmd_info,
        "smart": cmd_smart,
        "monitor": cmd_monitor,
        "export": cmd_export,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
