# Usage guide

## Requirements

- **Mac** (for flashing) with Python 3.9+ *or* the prebuilt binary; `diskutil`, `dd`, `ssh`, `scp` (all built in).
- Orin Nano dev kit, NVMe SSD (64 GB+ recommended) in a USB enclosure, USB stick (16 GB+) for JetPack 7.x.
- Internet access for the ~20 GB download (resumable) and, later, the Jetson's apt/SSH steps.
- Your Mac admin password (`sudo`) to write to the raw disk.

Run: `python3 jetson_flasher.py` or `./jetson-flasher-macos-arm64` (or `-x64`). Check version: `--version`.

## First screen: choose the board

`1` Orin Nano (NVMe supported) or `2` original Nano (microSD/USB only). Can be changed later with menu 5.

## Main menu

| Option | What it does |
|---|---|
| **1 List disks** | Shows external disks, NVMe enclosures, SD cards and their partitions. The Mac's boot disk is never shown. |
| **2 Check latest JetPack** | Scrapes NVIDIA's pages for the newest version and prints the official download URLs from the built-in catalog. Falls back to the catalog if offline. |
| **3 Download image only** | Downloads the SD-card image to `~/JetsonImages` with resume (`.part` file) and zip validation. |
| **4 Flash JetPack** | Download/choose file → pick disk → confirm → write → optional verify → eject. See below. |
| **5 Change board** | Switch Orin Nano / Nano. |
| **6 Firmware bridge** | Orin only. Prepares a JetPack 5.1.3 microSD to upgrade old firmware. |
| **7 Post-boot tuning** | SSH into the booted Jetson to update firmware and optimise it for AI. |

### Menu 4 – flashing (Orin Nano)

- **Option 1 – JetPack 7.2.1 (newest):** writes the installer ISO to a **USB stick** (not the NVMe). Boot the Jetson
  from the stick (Esc at NVIDIA logo → Boot Manager) and install onto the NVMe. Requires firmware 36.x already.
- **Option 2 – JetPack 6.2.1:** writes the system straight to the NVMe (or microSD). Then install the NVMe in the
  M.2 Key-M slot (bottom of the carrier board), remove the microSD, power on.

How the write works: the `.zip` is streamed (no extraction) into `sudo dd of=/dev/rdiskN bs=4m`. Afterwards the tool
unmounts, syncs, optionally **verifies** by comparing 20 random 16 MB blocks plus the first block against the image,
then ejects.

Safety prompts: size check (warns below the recommended size), full partition listing, and you must type the disk id.
You can supply your own `.zip/.img/.iso` instead of downloading ("Use a local file").

### Firmware bridge

A brand-new Orin Nano may ship with firmware older than 36.x, which cannot boot JetPack 6 (symptom: UEFI shell or
black screen). Fix:

1. Menu 6 → write JetPack 5.1.3 to a **microSD**.
2. Boot the Jetson from it, finish setup, reboot once (firmware → 35.5.0).
3. Menu 7 → *Update firmware / QSPI* installs the QSPI updater; reboot (firmware → 36.x).
4. Power off, remove the SD, boot the NVMe.

### Menu 7 – post-boot tuning over SSH

Give the Jetson's IP/hostname (default `192.168.55.1`, the USB-C device mode address) and username. The tool copies
`jetson_postinstall.sh` (also saved in the current folder so you can read it) via `scp`, then runs it with `sudo`
over `ssh -t`. Pick steps by number (Enter = the starred defaults):

| # | Step | Effect |
|---|---|---|
| 1* | Info | Model, L4T, kernel, boot device, disk/RAM/swap, power mode, firmware slots |
| 2* | Firmware | L4T 35.x: installs QSPI updater (→ 36.x); 36+/39: upgrades the bootloader package |
| 3* | Upgrade | `apt dist-upgrade`; JetPack 6 can retarget the apt repo (e.g. `36.5`) |
| 4* | JetPack SDK | `nvidia-jetpack` (CUDA, cuDNN, TensorRT, VPI) + CUDA in `PATH` |
| 5* | Resize | Grow root partition/filesystem to fill the NVMe |
| 6* | Power | MAXN / MAXN SUPER mode and `jetson_clocks` at boot (fan and clocks max) |
| 7* | Swap | Replaces zram with an NVMe swapfile (you choose GB, default 16), swappiness 10 |
| 8 | Headless | Boot to console, ≈800 MB more RAM |
| 9* | Docker | Docker with NVIDIA as default runtime; adds your user to `docker` |
| 10* | Tools | jtop, jetson-containers |
| 11 | Ollama | Local LLM server |

(* = default.) A reboot reminder is printed when needed, and the tool offers to reboot the Jetson.

## Windows

The Windows binary supports menus 2, 3, 5 and 7 (needs the built-in *OpenSSH Client* feature). Flashing is
macOS-only; on Windows write the downloaded image with balenaEtcher or Rufus.

## Troubleshooting

| Problem | Fix |
|---|---|
| No disk listed | Re-plug the enclosure; check it shows in Disk Utility. The Mac's own disk is hidden by design. |
| "Not a valid zip" after download | NVIDIA may have changed the link or require login: download in a browser and use "Use a local file". |
| Download interrupted | Run again; it resumes from the `.part` file. |
| `dd` permission/"Resource busy" | Close Finder windows of that disk; the tool force-unmounts, retry. |
| Verify reports differing samples | Re-flash; try another cable/enclosure/USB port. |
| Jetson shows UEFI shell/black screen | Old firmware → Firmware bridge. |
| scp/ssh fails | Check IP, that SSH is running, and the first-boot setup is finished. |
| Original Nano wants NVMe | Impossible: no M.2 slot; use microSD (or USB boot after setting boot order). |
| Binary blocked by macOS/Windows | See *Unsigned binaries* in the README. |

## Updating image links

NVIDIA publishes new images: edit the `CATALOG` dictionary at the top of `jetson_flasher.py`, bump `__version__`,
add a CHANGELOG entry and tag a release.
