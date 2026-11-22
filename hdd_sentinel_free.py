#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HDD Sentinel Free - CLI Disk Health Monitor (Open Source / Gratis)
Mirip HDD Sentinel: menampilkan kesehatan disk, suhu, SMART, jam menyala,
bad sector, umur disk, dan estimasi wear SSD/NVMe.

Platform: Windows (utama), Linux, macOS
Author: Open Source
"""

import argparse
import csv
import itertools
import json
import os
import platform
import re
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
        self._unicode_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠶", "⠷", "⠾", "⠿"]
        self._fallback_chars = ["-", "\\", "|", "/"]

    def _supports_unicode(self):
        try:
            enc = (sys.stdout.encoding or "").lower().replace("-", "")
            return enc in ("utf8", "utf-8")
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
        self.age_days = 0
        self.smart_attrs = {}
        self.smart_status = "Unknown"
        self.smart_available = False
        self.is_nvme = False
        self.used_bytes = 0
        self.free_bytes = 0
        self.reliability = {}

        # NVMe specific
        self.nvme_critical_warning = None
        self.nvme_available_spare = None
        self.nvme_available_spare_thresh = None
        self.nvme_percentage_used = None  # wear %
        self.nvme_data_units_read = None
        self.nvme_data_units_written = None
        self.nvme_host_read_commands = None
        self.nvme_host_write_commands = None
        self.nvme_power_cycles = None
        self.nvme_unsafe_shutdowns = None
        self.nvme_media_errors = None

        # ATA specific
        self.ata_total_lbas_read = None
        self.ata_total_lbas_written = None

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
            "is_nvme": self.is_nvme,
            "temperature_c": self.temperature,
            "health_percent": self.health,
            "power_on_hours": self.power_on_hours,
            "age_days": self.age_days,
            "age_human": format_age(self.age_days),
            "smart_status": self.smart_status,
            "smart_available": self.smart_available,
            "smart_attrs": self.smart_attrs,
            # NVMe
            "nvme_critical_warning": self.nvme_critical_warning,
            "nvme_available_spare": self.nvme_available_spare,
            "nvme_available_spare_thresh": self.nvme_available_spare_thresh,
            "nvme_percentage_used": self.nvme_percentage_used,
            "nvme_data_units_read": self.nvme_data_units_read,
            "nvme_data_units_written": self.nvme_data_units_written,
            "nvme_host_read_commands": self.nvme_host_read_commands,
            "nvme_host_write_commands": self.nvme_host_write_commands,
            "nvme_power_cycles": self.nvme_power_cycles,
            "nvme_unsafe_shutdowns": self.nvme_unsafe_shutdowns,
            "nvme_media_errors": self.nvme_media_errors,
            # ATA
            "ata_total_lbas_read": self.ata_total_lbas_read,
            "ata_total_lbas_written": self.ata_total_lbas_written,
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def run_cmd(cmd, timeout=30, shell=False):
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


def format_age(days):
    """Konversi hari menjadi representasi umur yang mudah dibaca (mirip HDD Sentinel)."""
    if not days:
        return "N/A"
    years = int(days // 365)
    remaining_days = int(days % 365)
    months = int(remaining_days // 30)
    days_left = int(remaining_days % 30)

    parts = []
    if years > 0:
        parts.append(f"{years} thn")
    if months > 0:
        parts.append(f"{months} bln")
    if days_left > 0 or not parts:
        parts.append(f"{days_left} hr")

    return " ".join(parts)


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
# Windows PowerShell reliability counter (fallback tanpa smartctl)
# ---------------------------------------------------------------------------
def get_windows_reliability_counter(device_num):
    """Coba ambil reliability counter via PowerShell Get-StorageReliabilityCounter."""
    info = {}
    ps = (
        f"Get-StorageReliabilityCounter -DiskNumber {device_num} | "
        f"Select-Object BytesRead,BytesWritten,Temperature,PowerOnHours,"
        f"ReallocatedSectors,ReadErrors,WriteErrors | ConvertTo-Json -Depth 2"
    )
    rc, out, _ = run_cmd(["powershell", "-Command", ps], shell=True)
    if rc != 0 or not out.strip():
        return info
    try:
        data = json.loads(out)
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            info["bytes_read"] = parse_size(data.get("BytesRead", 0))
            info["bytes_written"] = parse_size(data.get("BytesWritten", 0))
            info["temperature"] = parse_size(data.get("Temperature", 0))
            info["power_on_hours"] = parse_size(data.get("PowerOnHours", 0))
            info["reallocated_sectors"] = parse_size(data.get("ReallocatedSectors", 0))
            info["read_errors"] = parse_size(data.get("ReadErrors", 0))
            info["write_errors"] = parse_size(data.get("WriteErrors", 0))
    except (json.JSONDecodeError, IndexError):
        pass
    return info


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
        is_nvme = "NVMe" in str(iface).upper()
        health = item.get("HealthStatus", "") or "Unknown"
        op_status = item.get("OperationalStatus", [])
        if isinstance(op_status, list):
            status = ", ".join(str(s) for s in op_status) or health
        else:
            status = str(op_status) if op_status else health
        d = DiskInfo(
            device=device,
            model=model,
            serial=serial,
            size=size,
            interface=iface,
            type_=mediatype,
            status=status or "Unknown",
        )
        d.is_nvme = is_nvme
        disks.append(d)
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
# SMART parsing
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


def _parse_int_from_text(text):
    """Ambil integer pertama dari teks."""
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)", text.replace(",", ""))
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_nvme_smart(out):
    """Parsing output smartctl -a untuk drive NVMe (NVMe Log 0x02)."""
    info = {
        "smart_available": True,
        "attrs": {},
        "is_nvme": True,
        "nvme": {},
        "model": None,
        "serial": None,
        "smart_status": None,
    }

    for line in out.splitlines():
        line = line.strip()
        if "Device Model:" in line or "Model Number:" in line:
            info["model"] = line.split(":", 1)[1].strip()
        elif "Serial Number:" in line:
            info["serial"] = line.split(":", 1)[1].strip()
        elif "SMART overall-health self-assessment test result:" in line:
            info["smart_status"] = line.split(":", 1)[1].strip()

    # Parse NVMe specific fields
    nvme_fields = {
        "Critical Warning": "critical_warning",
        "Temperature": "temperature",
        "Available Spare": "available_spare",
        "Available Spare Threshold": "available_spare_thresh",
        "Percentage Used": "percentage_used",
        "Data Units Read": "data_units_read",
        "Data Units Written": "data_units_written",
        "Host Read Commands": "host_read_commands",
        "Host Write Commands": "host_write_commands",
        "Controller Busy Time": "controller_busy_time",
        "Power Cycles": "power_cycles",
        "Power On Hours": "power_on_hours",
        "Unsafe Shutdowns": "unsafe_shutdowns",
        "Media and Data Integrity Errors": "media_errors",
        "Error Information Log Entries": "error_log_entries",
    }

    for line in out.splitlines():
        for label, key in nvme_fields.items():
            if label in line:
                val = line.split(":", 1)[1].strip() if ":" in line else ""
                info["nvme"][key] = val
                break

    # Convert numeric values
    def to_int(val):
        if val is None:
            return None
        m = re.search(r"(\d[\d,]*)", str(val).replace(",", ""))
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
        return None

    pohr = to_int(info["nvme"].get("power_on_hours"))
    if pohr is not None:
        info["nvme"]["power_on_hours_int"] = pohr

    temp = to_int(info["nvme"].get("temperature"))
    if temp is not None:
        info["nvme"]["temperature_int"] = temp

    pct_used = to_int(info["nvme"].get("percentage_used"))
    if pct_used is not None:
        info["nvme"]["percentage_used_int"] = pct_used

    spare = to_int(info["nvme"].get("available_spare"))
    if spare is not None:
        info["nvme"]["available_spare_int"] = spare

    dur = to_int(info["nvme"].get("data_units_read"))
    if dur is not None:
        info["nvme"]["data_units_read_int"] = dur

    duw = to_int(info["nvme"].get("data_units_written"))
    if duw is not None:
        info["nvme"]["data_units_written_int"] = duw

    return info


def parse_ata_smart(out):
    """Parsing output smartctl -a untuk drive ATA/SATA (tabel atribut tradisional)."""
    info = {
        "smart_available": True,
        "attrs": {},
        "is_nvme": False,
        "nvme": {},
        "model": None,
        "serial": None,
        "smart_status": None,
    }

    for line in out.splitlines():
        if "Device Model:" in line:
            info["model"] = line.split(":", 1)[1].strip()
        elif "Serial Number:" in line:
            info["serial"] = line.split(":", 1)[1].strip()
        elif "SMART overall-health self-assessment test result:" in line:
            info["smart_status"] = line.split(":", 1)[1].strip()

    in_attrs = False
    for line in out.splitlines():
        if "ID# ATTRIBUTE_NAME" in line:
            in_attrs = True
            continue
        if in_attrs and line.strip():
            parts = line.split(None, 10)
            if len(parts) >= 10 and parts[0].isdigit():
                attr_id = int(parts[0])
                info["attrs"][attr_id] = {
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


def get_smart_smartctl(device):
    """Ambil data SMART via smartctl (ATA + NVMe)."""
    info = {"smart_available": False, "attrs": {}, "is_nvme": False, "nvme": {}}
    target = device
    if platform.system() == "Windows" and not target.startswith("\\\\.\\"):
        target = "\\\\.\\" + target

    rc, out, _ = run_cmd(["smartctl", "-a", target], timeout=30)
    if rc not in (0, 2) and not out:
        return info

    # Deteksi NVMe vs ATA berdasarkan output
    if "NVMe SMART/Health Information" in out or "NVMe Log 0x02" in out:
        info = parse_nvme_smart(out)
    else:
        info = parse_ata_smart(out)

    return info


def get_smart_windows_powershell(device):
    """Fallback Windows via PowerShell Get-PhysicalDisk jika smartctl tidak ada."""
    ps = (
        "Get-PhysicalDisk | Where-Object { $_.DeviceID -eq '%s' } | "
        "Select-Object DeviceID,FriendlyName,SerialNumber,HealthStatus,"
        "OperationalStatus,Size,MediaType | ConvertTo-Json -Depth 3"
    ) % device
    rc, out, _ = run_cmd(["powershell", "-Command", ps], shell=True)
    info = {"smart_available": False, "attrs": {}, "is_nvme": False, "nvme": {}}
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
        # Fallback: coba reliability counter + PhysicalDisk basic
        basic = get_smart_windows_powershell(disk.device)
        num = "".join(filter(str.isdigit, disk.device))
        reliability = {}
        if num:
            reliability = get_windows_reliability_counter(int(num))
        # Merge reliability into basic as pseudo-attrs
        basic["reliability"] = reliability
        return basic
    if system == "Linux" and smartctl_available():
        return get_smart_smartctl(f"/dev/{disk.device}")
    return {"smart_available": False, "attrs": {}, "is_nvme": False, "nvme": {}}


# ---------------------------------------------------------------------------
# SMART attribute parsing helpers
# ---------------------------------------------------------------------------
def get_temperature(smart):
    # NVMe
    nvme = smart.get("nvme", {})
    if nvme.get("temperature_int") is not None:
        return nvme["temperature_int"]
    # ATA
    attrs = smart.get("attrs", {})
    for aid in (194, 190):  # Temperature / Airflow Temperature
        if aid in attrs:
            return _raw_int(attrs[aid])
    return None


def get_power_on_hours(smart):
    # NVMe
    nvme = smart.get("nvme", {})
    if nvme.get("power_on_hours_int") is not None:
        return nvme["power_on_hours_int"]
    # ATA
    attrs = smart.get("attrs", {})
    if 9 in attrs:  # Power-On Hours
        return _raw_int(attrs[9])
    # Windows reliability fallback
    rel = smart.get("reliability", {})
    if rel.get("power_on_hours") is not None:
        return rel["power_on_hours"]
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

    # NVMe critical warning
    nvme = smart.get("nvme", {})
    cw = nvme.get("critical_warning")
    if cw is not None:
        try:
            cw_int = int(str(cw), 16) if str(cw).startswith("0x") else int(cw)
        except ValueError:
            cw_int = None
        if cw_int and cw_int != 0:
            health = min(health, 15)
            reasons.append(f"NVMe Critical Warning: {cw}")

    # Suhu
    temp = get_temperature(smart)
    if temp is not None:
        disk.temperature = temp
        if temp >= 60:
            health = min(health, 50)
            reasons.append(f"Suhu tinggi: {temp}\u00b0C")
        elif temp >= 50:
            health = min(health, 75)

    # Wear SSD/NVMe
    # NVMe Percentage Used
    pct_used = nvme.get("percentage_used_int")
    if pct_used is not None:
        disk.nvme_percentage_used = pct_used
        if pct_used >= 80:
            health = min(health, 30)
            reasons.append(f"NVMe wear terpakai: {pct_used}%")
        elif pct_used >= 50:
            health = min(health, 60)
            reasons.append(f"NVMe wear terpakai: {pct_used}%")

    # ATA SSD wear (231 SSD Life Left, 233 Media Wearout Indicator)
    for aid in (231, 233):
        if aid in attrs:
            try:
                val = int(attrs[aid].get("value", 100))
            except ValueError:
                val = 100
            if val < 20:
                health = min(health, 35)
                reasons.append(f"Usia SSD menipis (attr {aid})")

    # Windows reliability fallback - reallocated sectors
    rel = smart.get("reliability", {})
    if rel.get("reallocated_sectors", 0) > 0:
        health = min(health, 25)
        reasons.append(f"Reallocated sectors (PS): {rel['reallocated_sectors']}")

    return max(0, health), reasons


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
def enrich_disk(disk):
    smart = get_smart(disk)
    disk.smart_available = smart.get("smart_available", False)
    disk.smart_attrs = smart.get("attrs", {})
    disk.smart_status = smart.get("smart_status", "Unknown")
    disk.is_nvme = smart.get("is_nvme", False)

    # NVMe fields
    nvme = smart.get("nvme", {})
    disk.nvme_critical_warning = nvme.get("critical_warning")
    disk.nvme_available_spare = nvme.get("available_spare_int")
    disk.nvme_available_spare_thresh = nvme.get("available_spare_thresh")
    disk.nvme_percentage_used = nvme.get("percentage_used_int")
    disk.nvme_data_units_read = nvme.get("data_units_read_int")
    disk.nvme_data_units_written = nvme.get("data_units_written_int")
    disk.nvme_host_read_commands = nvme.get("host_read_commands")
    disk.nvme_host_write_commands = nvme.get("host_write_commands")
    disk.nvme_power_cycles = nvme.get("power_cycles")
    disk.nvme_unsafe_shutdowns = nvme.get("unsafe_shutdowns")
    disk.nvme_media_errors = nvme.get("media_errors")

    # ATA fields
    attrs = smart.get("attrs", {})
    if 241 in attrs:
        disk.ata_total_lbas_written = _raw_int(attrs[241])
    if 242 in attrs:
        disk.ata_total_lbas_read = _raw_int(attrs[242])

    if smart.get("model"):
        disk.model = smart["model"] or disk.model
    if smart.get("serial"):
        disk.serial = smart["serial"] or disk.serial
    if smart.get("size") and not disk.size:
        disk.size = smart["size"]

    disk.temperature = get_temperature(smart)
    disk.power_on_hours = get_power_on_hours(smart)

    # Windows reliability fallback for temperature/power_on_hours
    rel = smart.get("reliability", {})
    disk.reliability = rel
    if disk.temperature is None and rel.get("temperature") is not None:
        disk.temperature = rel["temperature"]
    if disk.power_on_hours == 0 and rel.get("power_on_hours") is not None:
        disk.power_on_hours = rel["power_on_hours"]

    disk.health, _ = compute_health(disk, smart)

    # Disk usage (used / free)
    used, free = get_disk_usage(disk)
    if used is not None:
        disk.used_bytes = used
    if free is not None:
        disk.free_bytes = free

    # Umur disk berdasarkan Power-On Hours
    if disk.power_on_hours > 0:
        disk.age_days = int(disk.power_on_hours / 24)

    return disk


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def _human_wear(val):
    if val is None:
        return "N/A"
    return f"{val}%"


def _human_data_units(val):
    if val is None:
        return "N/A"
    # smartctl usually reports in 512-byte units for NVMe, or GB for some versions
    # Show raw + approximate GB
    try:
        gb = val * 512 / (1024 ** 3)
        return f"{val:,} (~{gb:.2f} GB)"
    except (TypeError, ValueError):
        return f"{val:,}"


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

    for d in disks:
        enrich_disk(d)

    print(
        f"\n{BOLD}{'DEVICE':<14}{'MODEL':<22}{'TYPE':<6}{'SIZE':<10}"
        f"{'USED':<10}{'FREE':<10}{'TEMP':<7}{'HEALTH':<9}{'AGE':<10} STATUS{RESET}"
    )
    print("-" * 112)
    for d in disks:
        dev = d.device
        model = (d.model[:20] + "..") if len(d.model) > 20 else d.model
        dtype = d.type or "N/A"
        size = format_size(d.size)
        used = format_size(d.used_bytes) if d.used_bytes else "N/A"
        free = format_size(d.free_bytes) if d.free_bytes else "N/A"
        temp = format_temp(d.temperature)
        health = format_health(d.health)
        age = format_age(d.age_days)
        status = d.status
        print(
            f"{dev:<14}{model:<22}{dtype:<6}{size:<10}"
            f"{used:<10}{free:<10}{temp:<7}  {health}  {age:<10}  {status}"
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
    print(f"Jenis drive      : {'NVMe' if disk.is_nvme else 'ATA/SATA'}")
    if disk.used_bytes or disk.free_bytes:
        pct = (disk.used_bytes / disk.size * 100) if disk.size else 0
        print(f"Terpakai         : {format_size(disk.used_bytes)} ({pct:.1f}%)")
        print(f"Tersisa          : {format_size(disk.free_bytes)}")
    print(f"Status           : {disk.status}")
    print(f"SMART tersedia   : {'Ya' if disk.smart_available else 'Tidak'}")
    print(f"SMART status     : {disk.smart_status}")
    print(f"Suhu             : {format_temp(disk.temperature)}")
    print(f"Jam menyala      : {disk.power_on_hours} jam")
    print(f"Umur disk        : {format_age(disk.age_days)}")
    print(f"Kesehatan        : {format_health(disk.health)}")
    print()

    # Wear info (SSD/NVMe)
    if disk.is_nvme:
        print(f"{BOLD}Info NVMe:{RESET}")
        print(f"  Wear terpakai   : {_human_wear(disk.nvme_percentage_used)}")
        print(f"  Available Spare : {disk.nvme_available_spare}% (thresh {disk.nvme_available_spare_thresh}%)")
        print(f"  Data Units Read : {_human_data_units(disk.nvme_data_units_read)}")
        print(f"  Data Units Writ.: {_human_data_units(disk.nvme_data_units_written)}")
        print(f"  Power Cycles    : {disk.nvme_power_cycles if disk.nvme_power_cycles is not None else 'N/A'}")
        print(f"  Unsafe Shutdowns: {disk.nvme_unsafe_shutdowns if disk.nvme_unsafe_shutdowns is not None else 'N/A'}")
        print(f"  Media Errors    : {disk.nvme_media_errors if disk.nvme_media_errors is not None else 'N/A'}")
        if disk.nvme_critical_warning is not None:
            print(f"  Critical Warning: {disk.nvme_critical_warning}")
        print()
    else:
        print(f"{BOLD}Info ATA/SATA:{RESET}")
        print(f"  Total LBAs Read : {disk.ata_total_lbas_read if disk.ata_total_lbas_read is not None else 'N/A'}")
        print(f"  Total LBAs Writ.: {disk.ata_total_lbas_written if disk.ata_total_lbas_written is not None else 'N/A'}")
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

    print(f"\n{BOLD}SMART / HEALTH - {disk.device}{RESET}")
    print(f"Jenis drive      : {'NVMe' if disk.is_nvme else 'ATA/SATA'}")
    print(f"SMART tersedia   : {'Ya' if disk.smart_available else 'Tidak'}")
    print(f"SMART status     : {disk.smart_status}")
    print(f"Suhu             : {format_temp(disk.temperature)}")
    print(f"Jam menyala      : {disk.power_on_hours} jam")
    print(f"Umur disk        : {format_age(disk.age_days)}")
    print(f"Kesehatan        : {format_health(disk.health)}")
    print()

    if disk.is_nvme:
        print(f"{BOLD}NVMe SMART/Health Information (Log 0x02):{RESET}")
        nvme_fields = [
            ("Critical Warning", disk.nvme_critical_warning),
            ("Temperature", f"{disk.temperature}°C" if disk.temperature is not None else "N/A"),
            ("Available Spare", f"{disk.nvme_available_spare}%" if disk.nvme_available_spare is not None else "N/A"),
            ("Available Spare Thresh", f"{disk.nvme_available_spare_thresh}%" if disk.nvme_available_spare_thresh is not None else "N/A"),
            ("Percentage Used (Wear)", f"{disk.nvme_percentage_used}%" if disk.nvme_percentage_used is not None else "N/A"),
            ("Data Units Read", _human_data_units(disk.nvme_data_units_read)),
            ("Data Units Written", _human_data_units(disk.nvme_data_units_written)),
            ("Host Read Commands", disk.nvme_host_read_commands if disk.nvme_host_read_commands is not None else "N/A"),
            ("Host Write Commands", disk.nvme_host_write_commands if disk.nvme_host_write_commands is not None else "N/A"),
            ("Power Cycles", disk.nvme_power_cycles if disk.nvme_power_cycles is not None else "N/A"),
            ("Unsafe Shutdowns", disk.nvme_unsafe_shutdowns if disk.nvme_unsafe_shutdowns is not None else "N/A"),
            ("Media Errors", disk.nvme_media_errors if disk.nvme_media_errors is not None else "N/A"),
        ]
        max_label = max(len(k) for k, _ in nvme_fields)
        for label, val in nvme_fields:
            print(f"  {label:<{max_label}} : {val}")
    else:
        print(f"{BOLD}ATA SMART Attributes:{RESET}")
        print(
            f"{'ID':<5}{'NAMA':<28}{'FLAG':<8}{'VALUE':<9}{'WORST':<9}"
            f"{'THRESH':<9}{'TYPE':<10}{'UPDATED':<10}{'WHEN_FAILED':<14}{'RAW'}"
        )
        print("-" * 120)
        attrs = disk.smart_attrs
        for aid in sorted(attrs.keys()):
            a = attrs[aid]
            print(
                f"{aid:<5}{a.get('name',''):<28}{a.get('flag',''):<8}"
                f"{a.get('value',''):<9}{a.get('worst',''):<9}"
                f"{a.get('thresh',''):<9}{a.get('type',''):<10}"
                f"{a.get('updated',''):<10}{a.get('when_failed',''):<14}{a.get('raw','')}"
            )

    # Windows reliability fallback info
    rel = disk.reliability
    if rel and any(rel.values()):
        print()
        print(f"{BOLD}Windows Reliability Counter (fallback):{RESET}")
        print(f"  Bytes Read       : {format_size(rel.get('bytes_read', 0))}")
        print(f"  Bytes Written    : {format_size(rel.get('bytes_written', 0))}")
        print(f"  Temperature      : {rel.get('temperature', 'N/A')}°C")
        print(f"  Power On Hours   : {rel.get('power_on_hours', 'N/A')} jam")
        print(f"  Reallocated Sectors: {rel.get('reallocated_sectors', 'N/A')}")
        print(f"  Read Errors      : {rel.get('read_errors', 'N/A')}")
        print(f"  Write Errors     : {rel.get('write_errors', 'N/A')}")
    print()
    return 0


def cmd_health(args):
    """Ringkasan kesehatan semua disk."""
    disks = discover_disks()
    if not disks:
        print(c("Tidak ada disk fisik terdeteksi.", YELLOW))
        return 0

    print(f"\n{BOLD}RINGKASAN KESEHATAN DISK{RESET}")
    print(f"{'DEVICE':<14}{'MODEL':<22}{'HEALTH':<9}{'TEMP':<7}{'AGE':<10} STATUS")
    print("-" * 75)
    any_bad = False
    for d in disks:
        enrich_disk(d)
        dev = d.device
        model = (d.model[:20] + "..") if len(d.model) > 20 else d.model
        health = d.health
        temp = format_temp(d.temperature)
        age = format_age(d.age_days)
        status = d.status
        col = health_color(health)
        print(
            f"{dev:<14}{model:<22}{c(f'{health}%', col):<9} {temp:<7}{age:<10} {status}"
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
