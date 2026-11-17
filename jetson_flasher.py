#!/usr/bin/env python3
"""
Jetson NVMe Flasher for macOS
=============================

Interactive tool to:
  1. List the external disks connected to your Mac (e.g. an NVMe in a USB enclosure)
  2. Check the latest JetPack release for Jetson Orin Nano
  3. Download the official NVIDIA SD-card image (with resume + cache)
  4. Flash it straight to the NVMe (streams from the .zip, no 20 GB extraction)
  5. Verify the write
  6. Prepare a firmware "bridge" card (JetPack 5.1.3) for units with old QSPI firmware
  7. After the Jetson boots: update firmware/QSPI and tune it for AI over SSH

Only the Python standard library is used. Run it with:

    python3 jetson_flasher.py

You will be asked for your macOS password (sudo) when writing to the disk.

IMPORTANT
---------
* NVMe boot is a feature of the Jetson *Orin* Nano Developer Kit (and Super).
  The original 2019 Jetson Nano (Maxwell) has no M.2 NVMe slot and is limited
  to JetPack 4.6.x on microSD/USB. The script asks which board you have.
* NVIDIA's documented path for NVMe is SDK Manager (needs an Ubuntu x86 PC).
  Writing the SD-card image onto an NVMe works on the Orin Nano dev kit and is
  widely used, but it is not NVIDIA's officially documented method.
* A brand-new Orin Nano may ship with firmware older than 36.x, which cannot
  boot JetPack 6. Use menu option 6 (firmware bridge) in that case.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

__version__ = "2.0.1"

# --------------------------------------------------------------------------
# Catalog of official images. Edit here if NVIDIA publishes a new image.
# --------------------------------------------------------------------------

CACHE_DIR = Path.home() / "JetsonImages"

CATALOG = {
    "orin": {
        "label": "Jetson Orin Nano Developer Kit (8GB / 4GB / Super) - NVMe supported",
        "image": {
            "jetpack": "6.2.1",
            "l4t": "36.4.4",
            "url": "https://developer.nvidia.com/downloads/embedded/L4T/r36_Release_v4.4/jp62-r1-orin-nano-sd-card-image.zip",
            "note": "Latest SD-card image. Newer 6.2.x releases (e.g. 6.2.3 / L4T 36.5.x) "
                    "are installed on top of it with an APT upgrade (SSH tuning step).",
        },
        "iso": {
            "jetpack": "7.2.1",
            "l4t": "39.2.1",
            "url": "https://developer.nvidia.com/downloads/embedded/l4t/r39_release_v2.1/iso/jetsoninstaller-r39.2.1-2026-08-07-18-30-47-arm64.iso",
            "note": "Newest release (Ubuntu 24.04, CUDA 13, Super mode by default). NVIDIA no longer "
                    "ships an SD-card image for it: the ISO goes on a USB stick (16 GB+), the Jetson "
                    "boots from the stick and installs onto its NVMe or microSD. Needs firmware 36.x first.",
            "page": "https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.2.1",
        },
        "bridge": {
            "jetpack": "5.1.3",
            "l4t": "35.5.0",
            "url": "https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v5.0/jp513-orin-nano-sd-card-image_b29.zip",
            "page": "https://developer.nvidia.com/embedded/jetpack-sdk-513",
        },
        "min_size_gb": 64,
    },
    "nano": {
        "label": "Original Jetson Nano (2019, B01 / 2GB) - NO NVMe slot, microSD/USB only",
        "image": {
            "jetpack": "4.6.1",
            "l4t": "32.7.1",
            "url": "https://developer.nvidia.com/jetson-nano-sd-card-image",
            "note": "Final release for the original Nano. Ubuntu 18.04, CUDA 10.2.",
        },
        "bridge": None,
        "min_size_gb": 32,
    },
}

JETPACK_PAGES = [
    "https://developer.nvidia.com/embedded/jetpack",
    "https://developer.nvidia.com/embedded/jetpack-archive",
]

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) jetson-flasher/1.0"}

# --------------------------------------------------------------------------
# Small terminal UI helpers
# --------------------------------------------------------------------------

USE_COLOR = bool(sys.stdout and sys.stdout.isatty())
if USE_COLOR and sys.platform == "win32":
    os.system("")  # enables ANSI escape processing in the Windows console


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def ok(msg):   print(c("✔ ", "32") + msg)
def warn(msg): print(c("⚠ ", "33") + msg)
def err(msg):  print(c("✖ ", "31") + msg)
def info(msg): print(c("➜ ", "36") + msg)


def header(title: str):
    print()
    print(c("═" * 64, "35"))
    print(c(f"  {title}", "1;35"))
    print(c("═" * 64, "35"))


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(c(f"? {prompt}{suffix}: ", "1")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return val or (default or "")


def yes(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({d})").lower()
    if not val:
        return default
    return val in ("y", "yes", "s", "sim")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class Progress:
    def __init__(self, total: int, label: str):
        self.total, self.label = total, label
        self.done, self.start, self.last = 0, time.time(), 0.0

    def update(self, n: int):
        self.done += n
        now = time.time()
        if now - self.last < 0.25 and self.done < self.total:
            return
        self.last = now
        elapsed = max(now - self.start, 1e-6)
        speed = self.done / elapsed
        if self.total:
            pct = self.done / self.total
            bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
            eta = (self.total - self.done) / speed if speed else 0
            line = (f"\r  {self.label} {bar} {pct*100:5.1f}%  "
                    f"{human(self.done)}/{human(self.total)}  "
                    f"{human(speed)}/s  ETA {int(eta//60)}m{int(eta%60):02d}s ")
        else:
            line = f"\r  {self.label} {human(self.done)}  {human(speed)}/s "
        sys.stdout.write(line)
        sys.stdout.flush()

    def finish(self):
        self.update(0)
        print()


# --------------------------------------------------------------------------
# Disk discovery (macOS diskutil)
# --------------------------------------------------------------------------

@dataclass
class Disk:
    ident: str          # disk4
    size: int
    name: str
    protocol: str
    internal: bool
    removable: bool
    partitions: list
    kind: str = "Disk"

    @property
    def raw(self) -> str:
        return f"/dev/r{self.ident}"


def run(cmd, check=True, capture=True, **kw):
    return subprocess.run(cmd, check=check, capture_output=capture, text=False, **kw)


def require_macos() -> bool:
    """Disk listing/flashing relies on diskutil + dd, so it is macOS-only."""
    if sys.platform != "darwin":
        err("Listing and flashing disks is only supported on macOS (needs diskutil).")
        info("On Windows/Linux: use this tool to download the image and to tune the Jetson "
             "over SSH (menu 3 and 7), and write the image with balenaEtcher.")
        return False
    return True


def _info(ident: str) -> dict:
    return plistlib.loads(run(["diskutil", "info", "-plist", ident]).stdout)


def boot_disks() -> set[str]:
    """Whole-disk ids that hold the running macOS (never offered as targets)."""
    protected = {"disk0"}
    try:
        root = _info("/")
        parent = root.get("ParentWholeDisk")
        if parent:
            protected.add(parent)
            for store in _info(parent).get("APFSPhysicalStores", []) or []:
                dev = store.get("APFSPhysicalStore", "")
                m = re.match(r"(disk\d+)", dev)
                if m:
                    protected.add(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return protected


def classify(inf: dict) -> str:
    proto = (inf.get("BusProtocol") or "").lower()
    media = (inf.get("MediaName") or "").lower()
    if "secure digital" in proto or proto == "sd" or "sd card" in media or "sdxc" in media or "card reader" in media:
        return "SD card"
    if "nvme" in media or "nvme" in proto:
        return "NVMe"
    if "usb" in proto:
        return "USB disk"
    return "Disk"


def list_external_disks() -> list[Disk]:
    """External disks + SD cards (the Mac's built-in SD slot reports as 'internal')."""
    data = plistlib.loads(run(["diskutil", "list", "-plist", "physical"]).stdout)
    protected = boot_disks()
    disks = []
    for entry in data.get("AllDisksAndPartitions", []):
        ident = entry.get("DeviceIdentifier")
        if not ident or ident in protected:
            continue
        try:
            inf = _info(ident)
        except subprocess.CalledProcessError:
            continue
        if (inf.get("VirtualOrPhysical") == "Virtual"
                or (inf.get("BusProtocol") or "") == "Disk Image"):
            continue
        kind = classify(inf)
        internal = bool(inf.get("Internal", False))
        removable = bool(inf.get("RemovableMedia") or inf.get("Removable")
                         or inf.get("Ejectable") or inf.get("RemovableMediaOrExternalDevice"))
        # keep: anything external, or an internal SD slot / removable media
        if internal and not (removable or kind == "SD card"):
            continue
        parts = [
            f"{p.get('DeviceIdentifier')} {p.get('VolumeName') or p.get('Content', '')} "
            f"({human(p.get('Size', 0))})"
            for p in entry.get("Partitions", [])
        ]
        disks.append(Disk(
            ident=ident,
            size=inf.get("TotalSize") or inf.get("Size") or entry.get("Size", 0),
            name=inf.get("MediaName") or inf.get("IORegistryEntryName") or "Unknown",
            protocol=inf.get("BusProtocol") or inf.get("DeviceProtocol") or "?",
            internal=internal,
            removable=removable,
            partitions=parts,
            kind=kind,
        ))
    return disks


def show_disks() -> list[Disk]:
    if not require_macos():
        return []
    header("Disks you can flash (USB / NVMe enclosures / SD cards)")
    disks = list_external_disks()
    if not disks:
        warn("No external disk or SD card found. Plug in the USB-NVMe enclosure / insert the "
             "SD card and choose 1 again. (Your Mac's own disk is never listed.)")
        return []
    for i, d in enumerate(disks, 1):
        size = human(d.size) if d.size else "no media"
        print(f"  {c(str(i), '1;36')}) {c(d.ident, '1')}  {size:>10}  "
              f"{c(d.kind, '33'):<18} {d.name}  [{d.protocol}]")
        for p in d.partitions:
            print(f"        └ {p}")
    return disks


def pick_disk(min_gb: int) -> Disk | None:
    disks = show_disks()
    if not disks:
        return None
    sel = ask("Select the target disk number (or q to cancel)")
    if sel.lower() == "q" or not sel.isdigit() or not (1 <= int(sel) <= len(disks)):
        return None
    d = disks[int(sel) - 1]
    if d.ident in boot_disks():
        err("Refusing to touch the disk macOS is running from.")
        return None
    if not d.size:
        err("That slot has no card/media inserted.")
        return None
    if d.size < min_gb * 1e9 * 0.93:
        warn(f"{d.ident} is {human(d.size)}; NVIDIA recommends at least {min_gb} GB.")
        if not yes("Continue anyway?"):
            return None
    return d


# --------------------------------------------------------------------------
# Latest version check
# --------------------------------------------------------------------------

def http_get(url: str, timeout=20) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def vtuple(v: str):
    return tuple(int(x) for x in v.split("."))


def check_latest(board: str):
    header("Latest JetPack release")
    found = set()
    for url in JETPACK_PAGES:
        try:
            html = http_get(url).decode("utf-8", "ignore")
            found.update(re.findall(r"JetPack\s+(\d+\.\d+(?:\.\d+)?)", html))
        except Exception as e:  # noqa: BLE001
            warn(f"Could not read {url}: {e}")
    img = CATALOG[board]["image"]
    if found:
        versions = sorted(found, key=vtuple, reverse=True)
        info("Versions mentioned on NVIDIA's site: " + ", ".join(versions[:8]))
        if board == "orin":
            jp6 = [v for v in versions if v.startswith("6.")]
            newest6 = jp6[0] if jp6 else None
            if newest6:
                ok(f"Newest JetPack 6 release for Orin Nano: {c(newest6, '1')}")
            newer = [v for v in versions if vtuple(v) >= (7, 2)]
            if newer:
                ok(f"Newest JetPack overall: {c(newer[0], '1')} — supports Orin Nano from 7.2 on, "
                   "installed via a USB-stick ISO (menu 4 → option 1).")
                if vtuple(newer[0]) > vtuple(CATALOG['orin']['iso']['jetpack']):
                    warn(f"The built-in ISO link is {CATALOG['orin']['iso']['jetpack']}; update "
                         "CATALOG['orin']['iso']['url'] for the newer release.")
    else:
        warn("Online check failed; using the built-in catalog.")
    print()
    if board == "orin":
        iso = CATALOG["orin"]["iso"]
        ok(f"Option A – JetPack {iso['jetpack']} (L4T {iso['l4t']}) installer ISO → USB stick")
        print(f"   {iso['note']}")
        print(f"   {c(iso['url'], '2')}")
    ok(f"{'Option B – ' if board == 'orin' else ''}SD-card image flashed directly: "
       f"JetPack {img['jetpack']} (L4T {img['l4t']})")
    print(f"   {img['note']}")
    print(f"   {c(img['url'], '2')}")


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def download(url: str, dest_dir: Path = CACHE_DIR, on_progress=None) -> Path | None:
    """on_progress(done, total) replaces the terminal bar (used by the GUI)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Resolve redirects to learn the real filename and size
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            final = r.geturl()
            total = int(r.headers.get("Content-Length") or 0)
    except Exception:
        final, total = url, 0
    name = Path(urllib.parse.urlparse(final).path).name or "jetson-image.zip"
    if not name.endswith((".zip", ".img", ".iso", ".xz")):
        name += ".zip"
    dest = dest_dir / name
    part = dest.with_suffix(dest.suffix + ".part")

    if dest.exists() and (not total or dest.stat().st_size == total):
        ok(f"Already downloaded: {dest}")
        return dest

    have = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
        info(f"Resuming download at {human(have)}")
    info(f"Downloading {name} → {dest_dir}")
    try:
        req = urllib.request.Request(final, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:      # server ignored Range
                have = 0
            mode = "ab" if have else "wb"
            length = int(r.headers.get("Content-Length") or 0)
            grand = have + length if length else total
            prog = Progress(grand, "Download")
            prog.done = have
            with open(part, mode) as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    if on_progress:
                        prog.done += len(chunk)
                        on_progress(prog.done, grand)
                    else:
                        prog.update(len(chunk))
            if not on_progress:
                prog.finish()
    except (urllib.error.URLError, OSError) as e:
        err(f"Download failed: {e}. Run again to resume.")
        return None

    part.rename(dest)
    if dest.suffix == ".zip":
        if not zipfile.is_zipfile(dest):
            err("Downloaded file is not a valid zip (NVIDIA may have changed the link "
                "or requires login). Download it in your browser and choose 'use a local file'.")
            return None
    ok(f"Saved {dest} ({human(dest.stat().st_size)})")
    return dest


def choose_image(board: str, which: str = "image") -> Path | None:
    entry = CATALOG[board][which]
    print()
    print(f"  1) Download JetPack {entry['jetpack']} automatically")
    print("  2) Use a local .zip / .img / .iso file I already have")
    cached = ([p for ext in ("*.zip", "*.img", "*.iso") for p in sorted(CACHE_DIR.glob(ext))]
              if CACHE_DIR.exists() else [])
    if cached:
        print(f"     (cached in {CACHE_DIR}: {', '.join(p.name for p in cached)})")
    sel = ask("Choice", "1")
    if sel == "2":
        p = Path(ask("Path to file").strip("'\"")).expanduser()
        if not p.exists():
            err("File not found.")
            return None
        return p
    path = download(entry["url"])
    if path is None and entry.get("page"):
        info(f"Get it manually from {entry['page']} and use option 2.")
    return path


# --------------------------------------------------------------------------
# Flashing
# --------------------------------------------------------------------------

def open_image_stream(path: Path):
    """Return (file-like, size) for a .img or the .img inside a .zip."""
    if path.suffix == ".zip":
        z = zipfile.ZipFile(path)
        imgs = [m for m in z.infolist() if m.filename.endswith(".img")]
        if not imgs:
            raise RuntimeError("No .img inside the zip.")
        m = max(imgs, key=lambda m: m.file_size)
        return z.open(m), m.file_size
    return open(path, "rb"), path.stat().st_size


def sudo_warmup():
    info("Administrator password is needed to write to the raw disk.")
    run(["sudo", "-v"], capture=False)


def flash(image: Path, disk: Disk) -> bool:
    src, size = open_image_stream(image)
    if size > disk.size:
        err(f"Image ({human(size)}) is larger than the disk ({human(disk.size)}).")
        return False

    header("About to ERASE")
    print(f"  Disk   : {c(disk.ident, '1;31')}  {disk.name}  {human(disk.size)}  [{disk.protocol}]")
    for p in disk.partitions:
        print(f"           └ {p}")
    print(f"  Image  : {image.name}  ({human(size)} uncompressed)")
    warn("EVERYTHING on this disk will be destroyed.")
    if ask(f"Type the disk id ({disk.ident}) to confirm") != disk.ident:
        err("Confirmation did not match. Aborted.")
        return False

    sudo_warmup()
    info(f"Unmounting {disk.ident} …")
    run(["diskutil", "unmountDisk", "force", disk.ident], check=False)

    dd = subprocess.Popen(
        ["sudo", "dd", f"of={disk.raw}", "bs=4m"],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    prog = Progress(size, "Flash   ")
    hasher = hashlib.sha256()
    try:
        while chunk := src.read(4 << 20):
            hasher.update(chunk)
            dd.stdin.write(chunk)
            prog.update(len(chunk))
        dd.stdin.close()
        rc = dd.wait()
    except BrokenPipeError:
        rc = dd.wait()
    prog.finish()
    src.close()
    if rc != 0:
        err("dd failed: " + dd.stderr.read().decode(errors="ignore"))
        return False
    run(["sync"], check=False)
    ok(f"Write complete. Image SHA-256: {hasher.hexdigest()[:16]}…")

    if yes("Verify the write by sampling the disk (recommended, a few minutes)?", True):
        verify(image, disk, size)

    run(["diskutil", "eject", disk.ident], check=False)
    ok(f"{disk.ident} ejected. You can unplug it.")
    return True


def read_disk(disk: Disk, offset: int, length: int) -> bytes:
    """Read bytes from the raw disk via sudo dd (offset/length multiples of 1 MiB)."""
    mib = 1 << 20
    res = run(["sudo", "dd", f"if={disk.raw}", f"bs={mib}",
               f"skip={offset // mib}", f"count={length // mib}"], check=False)
    return res.stdout


def verify(image: Path, disk: Disk, size: int):
    mib = 1 << 20
    run(["diskutil", "unmountDisk", "force", disk.ident], check=False)
    samples = [0] + sorted(random.sample(range(1, max(size // (16 * mib), 2)), min(20, max(size // (16 * mib) - 1, 1))))
    prog = Progress(len(samples), "Verify  ")
    src, _ = open_image_stream(image)
    pos, bad = 0, 0
    for s in samples:
        off = s * 16 * mib
        # Skip forward in the (possibly compressed) stream
        while pos < off:
            pos += len(src.read(min(off - pos, 16 * mib)))
        want = src.read(16 * mib)
        pos += len(want)
        got = read_disk(disk, off, 16 * mib)[: len(want)]
        if got != want:
            bad += 1
        prog.update(1)
    prog.finish()
    src.close()
    if bad:
        err(f"{bad}/{len(samples)} samples differ. Re-flash the disk or try another cable/enclosure.")
    else:
        ok(f"All {len(samples)} sampled 16 MB blocks match.")


# --------------------------------------------------------------------------
# Remote (on the Jetson) script: firmware + AI tuning
# --------------------------------------------------------------------------

REMOTE_SCRIPT = r'''#!/usr/bin/env bash
# Generated by jetson_flasher.py - runs ON the Jetson
set -uo pipefail
G='\033[32m'; Y='\033[33m'; R='\033[31m'; B='\033[1m'; N='\033[0m'
step(){ echo -e "\n${B}==> $*${N}"; }
okk(){ echo -e "${G}✔ $*${N}"; }
wrn(){ echo -e "${Y}⚠ $*${N}"; }
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo ubuntu)}"
HOME_DIR=$(getent passwd "$TARGET_USER" | cut -d: -f6)
REBOOT_NEEDED=0

L4T=$(head -1 /etc/nv_tegra_release 2>/dev/null | sed -E 's/# R([0-9]+) \(release\), REVISION: ([0-9.]+).*/\1.\2/')
MODEL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null)

do_info(){
  step "System information"
  echo "Model        : $MODEL"
  echo "L4T          : ${L4T:-unknown}"
  echo "Kernel       : $(uname -r)"
  echo "Boot device  : $(findmnt -no SOURCE /)"
  echo "Disk usage   : $(df -h / | awk 'NR==2{print $3" used / "$2" ("$5")"}')"
  echo "Memory       : $(free -h | awk '/Mem/{print $2" RAM"}'), swap $(free -h | awk '/Swap/{print $2}')"
  echo "Power mode   : $(nvpmodel -q 2>/dev/null | head -1)"
  if command -v nvbootctrl >/dev/null; then
    echo "Firmware     :"; nvbootctrl dump-slots-info 2>/dev/null | sed 's/^/   /'
  fi
}

do_nvme(){
  step "NVMe diagnostics"
  apt-get install -y pciutils nvme-cli >/dev/null 2>&1 || wrn "Could not install pciutils/nvme-cli (no network?)"
  modprobe nvme 2>/dev/null || true
  echo "-- PCIe devices (the NVMe must appear as 'Non-Volatile memory controller'):"
  lspci 2>/dev/null || echo "   lspci unavailable"
  echo "-- Block devices:"; lsblk -o NAME,SIZE,TYPE,MODEL,MOUNTPOINT 2>/dev/null
  echo "-- nvme list:"; nvme list 2>/dev/null || true
  echo "-- Kernel messages (nvme/pcie):"
  dmesg 2>/dev/null | grep -i -E "nvme|pcie.*(link|fail|timeout)|tegra194-pcie" | tail -n 25
  echo "-- nvme driver: $(lsmod | grep -c '^nvme') module(s) loaded; kernel config: $(zcat /proc/config.gz 2>/dev/null | grep 'CONFIG_BLK_DEV_NVME=' || echo 'n/a')"
  FW=$(nvbootctrl dump-slots-info 2>/dev/null | awk -F': ' '/Current version/{print $2}')
  echo "-- Firmware: ${FW:-unknown}   L4T: ${L4T:-unknown}"
  if ls /dev/nvme0n1 >/dev/null 2>&1; then
    okk "NVMe detected: /dev/nvme0n1. The kernel driver is fine."
    [[ "${FW:-0}" =~ ^3[0-5]\. ]] && wrn "Firmware ${FW} is older than 36.x: update it (--firmware) or JetPack 6/7 installers will not boot."
  else
    wrn "No NVMe device. The driver is built into the Jetson kernel, so this is almost always:"
    echo "   1. SSD not fully seated / screw missing in the M.2 Key-M slot (bottom of the carrier board)"
    echo "   2. SSD is SATA M.2 (B-key) - only PCIe NVMe works"
    echo "   3. Old QSPI firmware - run the firmware step (--firmware), power off completely, retry"
    echo "   4. Try another NVMe (some drives with power-saving/PCIe Gen4 quirks fail to link)"
  fi
}

do_firmware(){
  step "Firmware / QSPI bootloader"
  FW=$(nvbootctrl dump-slots-info 2>/dev/null | awk -F': ' '/Current version/{print $2}')
  echo "Current firmware: ${FW:-unknown}   L4T: $L4T"
  case "$L4T" in
    35.*)
      if [[ "$FW" == 35.5* ]]; then
        step "Firmware is 35.5 -> installing QSPI updater (moves firmware to 36.x)"
        apt-get update && apt-get install -y nvidia-l4t-jetson-orin-nano-qspi-updater \
          && { okk "QSPI update scheduled. REBOOT, wait for it to finish, then power off and boot the JetPack 6 NVMe."; REBOOT_NEEDED=1; }
      else
        wrn "Firmware is older than 35.5. Rebooting once lets JetPack 5.1.3 update it to 35.5; then run this step again."
        systemctl status nv-l4t-bootloader-config --no-pager | head -5 || true
        REBOOT_NEEDED=1
      fi ;;
    36.*|38.*|39.*)
      step "JetPack 6/7 detected - updating bootloader package (capsule update on next reboot if newer)"
      apt-get update
      apt-get install -y --only-upgrade nvidia-l4t-bootloader && okk "Bootloader package up to date."
      REBOOT_NEEDED=1 ;;
    *) wrn "Unknown L4T release; skipping." ;;
  esac
}

do_upgrade(){
  step "System upgrade (apt) ${UPGRADE_TO:+to L4T r$UPGRADE_TO}"
  SRC=/etc/apt/sources.list.d/nvidia-l4t-apt-source.list
  if [[ -n "${UPGRADE_TO:-}" && -f $SRC ]]; then
    cp $SRC $SRC.bak
    sed -i -E "s#/r[0-9]+\.[0-9]+#/r${UPGRADE_TO}#g" $SRC
    echo "APT source now:"; cat $SRC
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confold" dist-upgrade
  okk "System upgraded."; REBOOT_NEEDED=1
}

do_jetpack(){
  step "Installing full JetPack SDK (CUDA, cuDNN, TensorRT, VPI, samples)"
  apt-get update && apt-get install -y nvidia-jetpack python3-pip
  grep -q 'cuda/bin' "$HOME_DIR/.bashrc" || cat >> "$HOME_DIR/.bashrc" <<'EOF'
# CUDA (added by jetson_flasher)
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}
EOF
  okk "JetPack installed. nvcc: $(/usr/local/cuda/bin/nvcc --version 2>/dev/null | tail -1)"
}

do_resize(){
  step "Make sure the root filesystem uses the whole NVMe"
  ROOTDEV=$(findmnt -no SOURCE /)
  DISK="/dev/$(lsblk -no PKNAME "$ROOTDEV")"; PARTNUM=$(echo "$ROOTDEV" | grep -oE '[0-9]+$')
  apt-get install -y cloud-guest-utils >/dev/null 2>&1 || true
  growpart "$DISK" "$PARTNUM" 2>&1 | grep -v NOCHANGE || true
  resize2fs "$ROOTDEV" >/dev/null 2>&1 || true
  okk "Root: $(df -h / | awk 'NR==2{print $2" total, "$4" free"}')"
}

do_power(){
  step "Max performance power mode"
  CONF=/etc/nvpmodel.conf
  ID=$(grep -E 'POWER_MODEL ID=[0-9]+ NAME=MAXN_SUPER' $CONF | grep -oE 'ID=[0-9]+' | cut -d= -f2 | head -1)
  [[ -z "$ID" ]] && ID=$(grep -E 'POWER_MODEL ID=[0-9]+ NAME=MAXN' $CONF | grep -oE 'ID=[0-9]+' | cut -d= -f2 | head -1)
  if [[ -n "$ID" ]]; then
    yes | nvpmodel -m "$ID" >/dev/null 2>&1 || nvpmodel -m "$ID"
    okk "nvpmodel set to mode $ID ($(grep -E "POWER_MODEL ID=$ID " $CONF | grep -oE 'NAME=[^ ]+'))"
  else
    wrn "No MAXN mode in $CONF. Available:"; grep POWER_MODEL $CONF
  fi
  cat >/etc/systemd/system/jetson-clocks.service <<'EOF'
[Unit]
Description=Lock Jetson clocks to max (jetson_clocks)
After=nvpmodel.service
[Service]
Type=oneshot
ExecStart=/usr/bin/jetson_clocks
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload && systemctl enable --now jetson-clocks.service
  okk "jetson_clocks enabled at boot (fan + clocks at max)."
}

do_swap(){
  step "Swap: disable zram, create ${SWAP_GB}G swapfile on NVMe"
  systemctl disable --now nvzramconfig 2>/dev/null || true
  if ! swapon --show | grep -q /swapfile; then
    fallocate -l "${SWAP_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  sysctl -w vm.swappiness=10 >/dev/null; echo 'vm.swappiness=10' > /etc/sysctl.d/90-jetson-ai.conf
  okk "Swap: $(free -h | awk '/Swap/{print $2}')"
}

do_headless(){
  step "Boot to console (frees ~800MB RAM for models)"
  systemctl set-default multi-user.target
  okk "Desktop disabled. Re-enable with: sudo systemctl set-default graphical.target"
  REBOOT_NEEDED=1
}

do_docker(){
  step "Docker + NVIDIA container runtime"
  command -v docker >/dev/null || { apt-get update && apt-get install -y docker.io; }
  apt-get install -y nvidia-container-toolkit 2>/dev/null || apt-get install -y nvidia-container
  if command -v nvidia-ctk >/dev/null; then nvidia-ctk runtime configure --runtime=docker --set-as-default; fi
  python3 - <<'EOF'
import json, os
p = "/etc/docker/daemon.json"
d = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
d.setdefault("runtimes", {})["nvidia"] = {"path": "nvidia-container-runtime", "runtimeArgs": []}
d["default-runtime"] = "nvidia"
json.dump(d, open(p, "w"), indent=2)
EOF
  usermod -aG docker "$TARGET_USER"
  systemctl restart docker && systemctl enable docker
  okk "Docker ready (default runtime: nvidia). Log out/in to use docker without sudo."
}

do_tools(){
  step "Monitoring tools (jtop) and jetson-containers"
  apt-get install -y python3-pip git curl
  pip3 install -U jetson-stats 2>/dev/null || pip3 install -U --break-system-packages jetson-stats
  systemctl restart jtop.service 2>/dev/null || true
  if [[ ! -d "$HOME_DIR/jetson-containers" ]]; then
    sudo -u "$TARGET_USER" git clone --depth=1 https://github.com/dusty-nv/jetson-containers "$HOME_DIR/jetson-containers"
  fi
  bash "$HOME_DIR/jetson-containers/install.sh" || wrn "jetson-containers install.sh reported an error"
  okk "Run 'jtop' to monitor. Example: jetson-containers run \$(autotag l4t-pytorch)"
}

do_ollama(){
  step "Ollama (local LLM server, CUDA-enabled on Jetson)"
  curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama 2>/dev/null || true
  okk "Try: ollama run llama3.2:3b"
}

for a in "$@"; do
  case $a in
    --info) do_info ;;
    --firmware) do_firmware ;;
    --nvme) do_nvme ;;
    --upgrade) do_upgrade ;;
    --jetpack) do_jetpack ;;
    --resize) do_resize ;;
    --power) do_power ;;
    --swap) do_swap ;;
    --headless) do_headless ;;
    --docker) do_docker ;;
    --tools) do_tools ;;
    --ollama) do_ollama ;;
  esac
done
echo
okk "Done."
if [[ $REBOOT_NEEDED == 1 ]]; then wrn "A reboot is required: sudo reboot"; fi
'''

REMOTE_STEPS = [
    ("--info",     "Show system / firmware info", True),
    ("--nvme",     "NVMe diagnostics (is the SSD seen? driver, PCIe, firmware hints)", True),
    ("--firmware", "Update firmware / QSPI bootloader", True),
    ("--upgrade",  "APT full upgrade (JetPack 6 only: optionally move repo to 36.5 = JetPack 6.2.3)", True),
    ("--jetpack",  "Install full JetPack SDK (CUDA, cuDNN, TensorRT) + CUDA PATH", True),
    ("--resize",   "Expand root partition to the full NVMe", True),
    ("--power",    "MAXN / MAXN SUPER power mode + jetson_clocks at boot", True),
    ("--swap",     "Replace zram with a swapfile on NVMe (helps big models)", True),
    ("--headless", "Boot without desktop (≈800 MB more RAM for models)", False),
    ("--docker",   "Docker with NVIDIA runtime as default", True),
    ("--tools",    "jtop + jetson-containers (PyTorch, LLM, VLM containers)", True),
    ("--ollama",   "Ollama LLM server", False),
]


def flow_nvme_help():
    header("NVMe not detected by the Jetson / installer")
    print("  The NVMe driver is already inside NVIDIA's Linux kernel - there is nothing to install on the")
    print("  USB stick. When the installer lists no NVMe the cause is one of:")
    print("   1. SSD not seated / not screwed down in the M.2 Key-M slot under the carrier board.")
    print("   2. SSD is not PCIe NVMe (M.2 SATA drives are not supported).")
    print("   3. Firmware (QSPI) older than 36.x - the usual cause on a new Orin Nano.")
    print("   4. A drive that fails PCIe link training - try a different NVMe.")
    print()
    print("  Safe order of attack:")
    print("   a) Power off, re-seat the SSD, boot the installer again.")
    print("   b) Still missing? Make the JetPack 5.1.3 microSD (menu 6), boot it, run this menu's SSH")
    print("      diagnostics (it also updates firmware to 36.x), then retry the JetPack 7.2.x USB stick.")
    print()
    if yes("Run NVMe diagnostics + firmware update on a booted Jetson over SSH now?"):
        remote_menu(preset=["--info", "--nvme", "--firmware"])
    elif yes("Prepare the JetPack 5.1.3 firmware-bridge microSD now?"):
        flow_bridge()


def remote_menu(preset=None):
    header("Firmware update & AI tuning on the Jetson (via SSH)")
    print("  The Jetson must be booted, on your network, with SSH enabled")
    print("  (finish the first-boot setup on a monitor, or over the USB-C port: 192.168.55.1).")
    host = ask("Jetson IP or hostname", "192.168.55.1")
    user = ask("Jetson username", "nvidia")
    target = f"{user}@{host}"

    print("\n  Select steps (enter numbers separated by spaces, Enter = defaults marked *):")
    for i, (_, label, default) in enumerate(REMOTE_STEPS, 1):
        print(f"   {'*' if default else ' '} {i:2}) {label}")
    sel = [] if preset else ask("Steps").split()
    if preset:
        chosen = preset
    elif sel:
        chosen = [REMOTE_STEPS[int(s) - 1][0] for s in sel if s.isdigit() and 1 <= int(s) <= len(REMOTE_STEPS)]
    else:
        chosen = [flag for flag, _, d in REMOTE_STEPS if d]

    env = []
    if "--swap" in chosen:
        env.append(f"SWAP_GB={ask('Swapfile size in GB', '16')}")
    if "--upgrade" in chosen:
        to = ask("JetPack 6 only – move APT repo to L4T release (e.g. 36.5), Enter = keep", "")
        if to:
            env.append(f"UPGRADE_TO={to}")

    local = Path.cwd() / "jetson_postinstall.sh"
    with open(local, "w", newline="\n") as fh:   # LF endings even on Windows
        fh.write(REMOTE_SCRIPT)
    local.chmod(0o755)
    ok(f"Remote script saved locally too: {local}")

    info(f"Copying script to {target} …")
    if run(["scp", "-o", "StrictHostKeyChecking=accept-new", str(local),
            f"{target}:~/jetson_postinstall.sh"], check=False, capture=False).returncode != 0:
        err("scp failed. Check IP, user and that SSH is running on the Jetson.")
        return
    cmd = f"sudo {' '.join(env)} bash ~/jetson_postinstall.sh {' '.join(chosen)}"
    info(f"Running on Jetson: {cmd}")
    run(["ssh", "-t", target, cmd], check=False, capture=False)
    if yes("Reboot the Jetson now?"):
        run(["ssh", "-t", target, "sudo reboot"], check=False, capture=False)


# --------------------------------------------------------------------------
# Privileged flash worker (used by the GUI; runs as root via the OS admin prompt)
# --------------------------------------------------------------------------

def _status(path: str, **kw):
    with open(path, "a") as f:
        f.write(json.dumps(kw) + "\n")


class _RawWriter:
    """Raw-disk writer. If macOS refuses open() (EPERM), fall back to Apple's /bin/dd."""

    def __init__(self, path: str):
        self.proc, self.f = None, None
        try:
            self.f = open(path, "wb", buffering=0)
        except PermissionError:
            self.proc = subprocess.Popen(["/bin/dd", f"of={path}", "bs=4m"],
                                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def _fail(self):
        err = self.proc.stderr.read().decode(errors="ignore").strip() if self.proc.stderr else ""
        self.proc.wait()
        raise PermissionError(1, err or "Operation not permitted")

    def write(self, mv):
        if self.f:
            while mv:
                mv = mv[self.f.write(mv):]
            return
        try:
            self.proc.stdin.write(bytes(mv))
        except (BrokenPipeError, OSError):
            self._fail()

    def close(self):
        if self.f:
            try:
                os.fsync(self.f.fileno())
            except OSError:
                pass
            self.f.close()
        else:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            if self.proc.wait() != 0:
                self._fail()


class _RawReader:
    def __init__(self, path: str, size: int, blk: int):
        self.proc, self.f = None, None
        try:
            self.f = open(path, "rb", buffering=0)
        except PermissionError:
            count = (size + blk - 1) // blk
            self.proc = subprocess.Popen(["/bin/dd", f"if={path}", "bs=4m", f"count={count}"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.f = self.proc.stdout

    def read(self, n):
        data = self.f.read(n)
        if not data and self.proc:
            err = self.proc.stderr.read().decode(errors="ignore")
            if "not permitted" in err:
                raise PermissionError(1, err.strip())
        return data

    def close(self):
        self.f.close()


def worker_flash(image: str, ident: str, status: str, do_verify: bool) -> int:
    """Write `image` to /dev/r<ident>, report JSON lines to `status`. Needs root."""
    cancel = status + ".cancel"
    try:
        if sys.platform != "darwin" or os.geteuid() != 0:
            raise RuntimeError("The flash worker must run as root on macOS.")
        if not re.fullmatch(r"disk\d+", ident):
            raise RuntimeError(f"Invalid disk id: {ident}")
        allowed = {d.ident: d for d in list_external_disks()}
        if ident not in allowed or ident in boot_disks():
            raise RuntimeError(f"{ident} is not an external disk (refusing to write).")
        disk = allowed[ident]
        src, size = open_image_stream(Path(image))
        if size > disk.size:
            raise RuntimeError(f"Image ({human(size)}) is larger than the disk ({human(disk.size)}).")
        run(["diskutil", "unmountDisk", "force", ident], check=False)
        blk, sector = 4 << 20, 4096
        h, done, last = hashlib.sha256(), 0, 0.0
        dev = _RawWriter(disk.raw)
        try:
            while chunk := src.read(blk):
                if os.path.exists(cancel):
                    raise RuntimeError("Cancelled. The disk is left partially written.")
                h.update(chunk)
                done += len(chunk)
                if len(chunk) % sector:
                    chunk += b"\0" * (sector - len(chunk) % sector)
                dev.write(memoryview(chunk))
                if time.time() - last > 0.3:
                    last = time.time()
                    _status(status, phase="write", done=done, total=size)
        finally:
            dev.close()
        src.close()
        _status(status, phase="write", done=size, total=size)
        if do_verify:
            hv, got, last = hashlib.sha256(), 0, 0.0
            dev = _RawReader(disk.raw, size, blk)
            try:
                while got < size:
                    if os.path.exists(cancel):
                        raise RuntimeError("Verification cancelled.")
                    want = min(blk, size - got)
                    data = dev.read(((want + sector - 1) // sector) * sector)[:want]
                    if not data:
                        break
                    hv.update(data)
                    got += len(data)
                    if time.time() - last > 0.3:
                        last = time.time()
                        _status(status, phase="verify", done=got, total=size)
            finally:
                dev.close()
            if hv.hexdigest() != h.hexdigest():
                raise RuntimeError("Verification FAILED: the disk content differs from the image. "
                                   "Try another cable / enclosure / USB port and flash again.")
        run(["diskutil", "eject", ident], check=False)
        _status(status, phase="done", sha256=h.hexdigest(), verified=do_verify)
        return 0
    except Exception as e:  # noqa: BLE001
        _status(status, phase="error", msg=str(e),
                code="eperm" if isinstance(e, PermissionError) else "")
        return 1


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _ssh_env(password: str):
    """Environment that lets ssh/scp read the password without a terminal (SSH_ASKPASS)."""
    env, tmp = dict(os.environ), None
    if password:
        tmp = Path(tempfile.mkdtemp(prefix="jfask"))
        if sys.platform == "win32":
            ap = tmp / "askpass.bat"
            ap.write_text("@echo off\r\necho %JF_PW%\r\n")
        else:
            ap = tmp / "askpass.sh"
            ap.write_text('#!/bin/sh\nprintf "%s\\n" "$JF_PW"\n')
            ap.chmod(0o700)
        env.update(SSH_ASKPASS=str(ap), SSH_ASKPASS_REQUIRE="force",
                   JF_PW=password, DISPLAY=env.get("DISPLAY", ":0"))
    return env, tmp


SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
            "-o", "NumberOfPasswordPrompts=1"]


def run_remote(host: str, user: str, password: str, steps: list, extra_env: list,
               log, reboot: bool = False) -> int:
    """Copy jetson_postinstall.sh to the Jetson and run it with sudo, streaming output to log()."""
    target = f"{user}@{host}"
    env, tmp = _ssh_env(password)
    try:
        script = Path(tempfile.gettempdir()) / "jetson_postinstall.sh"
        with open(script, "w", newline="\n") as fh:
            fh.write(REMOTE_SCRIPT)
        if not reboot:
            log(f"Copying script to {target} ...")
            r = subprocess.run(["scp", *SSH_OPTS, str(script), f"{target}:~/jetson_postinstall.sh"],
                               env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if r.returncode != 0:
                log("scp failed: " + (r.stderr or r.stdout).strip())
                return r.returncode
            remote = ("sudo -S -p '' env " + " ".join(extra_env) +
                      " bash ~/jetson_postinstall.sh " + " ".join(steps))
        else:
            remote = "sudo -S -p '' reboot"
        log(f"Running on the Jetson: {' '.join(steps) if not reboot else 'reboot'}")
        pr = subprocess.Popen(["ssh", *SSH_OPTS, target, remote], env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1)
        pr.stdin.write(password + "\n")
        pr.stdin.close()
        for line in pr.stdout:
            log(ANSI_RE.sub("", line.rstrip("\n")))
        return pr.wait()
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Guided flows
# --------------------------------------------------------------------------

def flow_flash(board: str):
    if not require_macos():
        return
    header("Flash JetPack")
    cfg = CATALOG[board]
    if board == "nano":
        warn("The original Jetson Nano cannot boot from NVMe. Flash a microSD card "
             "(or a USB disk, after setting the USB boot order on the Nano).")
        image = choose_image(board)
        disk = pick_disk(cfg["min_size_gb"]) if image else None
        if disk:
            flash(image, disk)
        return

    iso, img = cfg["iso"], cfg["image"]
    print(f"  1) JetPack {iso['jetpack']} (newest) – write installer ISO to a USB stick")
    print(f"  2) JetPack {img['jetpack']} – write the system straight to the NVMe / microSD")
    mode = ask("Choice", "1")

    if mode == "1":
        info(iso["note"])
        image = choose_image(board, "iso")
        if not image:
            return
        info("Now pick the USB stick (16 GB+). NOT the NVMe – the installer runs on the Jetson.")
        disk = pick_disk(16)
        if disk and flash(image, disk):
            header("Next steps (JetPack 7.2.x)")
            print("  0. Firmware must already be 36.x (JetPack 6). New unit? Do menu 6 first.")
            print("  1. Install the NVMe in the Jetson's M.2 Key-M slot (bottom of the carrier board).")
            print("  2. Plug the USB stick into the Jetson, connect monitor + keyboard, power on.")
            print("  3. Press Esc at the NVIDIA logo → Boot Manager → choose the USB stick.")
            print("  4. In the installer, pick the NVMe as the target. Remove the stick after it finishes.")
            print("  5. Then use menu 7 to tune for AI.")
            print("  No NVMe in the installer? -> menu 8 (diagnose + firmware).")
        return

    image = choose_image(board)
    if not image:
        return
    disk = pick_disk(cfg["min_size_gb"])
    if disk and flash(image, disk):
        header("Next steps")
        if disk.kind == "SD card":
            print("  1. Put the microSD card in the slot under the Jetson module.")
        else:
            print("  1. Install the NVMe in the M.2 Key-M slot on the bottom of the carrier board")
            print("     (remove the microSD card so the Jetson boots from NVMe).")
        print("  2. Power on with monitor+keyboard, finish the Ubuntu first-boot setup.")
        print("  3. NVMe not seen or UEFI shell / black screen? Firmware is too old (menu 8 / 6):")
        print("     use menu option 6 (firmware bridge).")
        print("  4. Then use menu option 7 to update firmware and tune for AI.")


def flow_bridge():
    if not require_macos():
        return
    header("Firmware bridge for a new Orin Nano (firmware < 36.x)")
    print(" Required ONLY when JetPack 6 doesn't boot (UEFI shell). Steps:")
    print("  a) This tool writes JetPack 5.1.3 to a *microSD card* (QSPI updater requires SD boot).")
    print("  b) Boot the Jetson from that SD, finish setup, reboot once (firmware → 35.5.0).")
    print("  c) Menu 7 → 'Update firmware' installs the QSPI updater; reboot (firmware → 36.x).")
    print("  d) Power off, remove the SD card, boot from the JetPack 6 NVMe.")
    if not yes("Prepare the JetPack 5.1.3 microSD now?", True):
        return
    image = choose_image("orin", "bridge")
    if not image:
        return
    disk = pick_disk(32)
    if disk:
        flash(image, disk)


def choose_board() -> str:
    header("Which Jetson do you have?")
    keys = list(CATALOG)
    for i, k in enumerate(keys, 1):
        print(f"  {i}) {CATALOG[k]['label']}")
    print(c("\n  Tip: if your board has an M.2 slot underneath for the NVMe, it is an Orin Nano.", "2"))
    sel = ask("Board", "1")
    return keys[int(sel) - 1] if sel.isdigit() and 1 <= int(sel) <= len(keys) else "orin"


def main():
    if "--version" in sys.argv[1:]:
        print(f"jetson-flasher {__version__}")
        return
    if len(sys.argv) >= 6 and sys.argv[1] == "--worker-flash":
        sys.exit(worker_flash(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] == "1"))
    needed = ("diskutil", "dd", "ssh", "scp") if sys.platform == "darwin" else ("ssh", "scp")
    if sys.platform != "darwin":
        warn("Not running on macOS: disk flashing is disabled; download and SSH tuning still work.")
    for tool in needed:
        if not shutil.which(tool):
            err(f"'{tool}' not found in PATH.")
            sys.exit(1)
    print(c(r"""
     _      _                     _____ _           _
    | | ___| |_ ___  ___  _ __   |  ___| | __ _ ___| |__   ___ _ __
 _  | |/ _ \ __/ __|/ _ \| '_ \  | |_  | |/ _` / __| '_ \ / _ \ '__|
| |_| |  __/ |_\__ \ (_) | | | | |  _| | | (_| \__ \ | | |  __/ |
 \___/ \___|\__|___/\___/|_| |_| |_|   |_|\__,_|___/_| |_|\___|_|
""", "32"))
    board = choose_board()
    while True:
        header(f"Main menu — {CATALOG[board]['label'].split(' - ')[0]}")
        print("  1) List connected disks")
        print("  2) Check latest JetPack version")
        print("  3) Download image only")
        print("  4) Flash JetPack (NVMe / SD card / USB installer stick)")
        print("  5) Change board")
        if board == "orin":
            print("  6) Firmware bridge: prepare JetPack 5.1.3 microSD (old firmware)")
            print("  8) NVMe not detected? Diagnose + firmware fix")
        print("  7) Jetson is booted → update firmware & tune for AI (SSH)")
        print("  q) Quit")
        sel = ask("Choice")
        try:
            if sel == "1":
                show_disks()
            elif sel == "2":
                check_latest(board)
            elif sel == "3":
                download(CATALOG[board]["image"]["url"])
            elif sel == "4":
                flow_flash(board)
            elif sel == "5":
                board = choose_board()
            elif sel == "6" and board == "orin":
                flow_bridge()
            elif sel == "7":
                remote_menu()
            elif sel == "8" and board == "orin":
                flow_nvme_help()
            elif sel.lower() in ("q", "quit", "exit"):
                break
        except subprocess.CalledProcessError as e:
            err(f"Command failed: {' '.join(map(str, e.cmd))}")
        except KeyboardInterrupt:
            print()
            warn("Cancelled.")


if __name__ == "__main__":
    main()
