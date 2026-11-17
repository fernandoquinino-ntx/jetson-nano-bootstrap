# Jetson Bootstrap – NVMe flasher & AI tuner

A **graphical step-by-step wizard** (macOS and Windows) that takes a **NVIDIA Jetson Orin Nano**
(or original Jetson Nano) from "box" to "ready for AI":

1. downloads the official JetPack image (resumable, cached in `~/JetsonImages`),
2. writes it to your NVMe / microSD / USB disk and verifies the write (macOS),
3. prepares a **firmware bridge** microSD (JetPack 5.1.3) for units with old QSPI firmware,
4. diagnoses an NVMe that is not detected,
5. once the Jetson is booted, connects over **SSH** to update firmware and tune it for AI
   (CUDA/TensorRT, MAXN power mode, swap, Docker + NVIDIA runtime, jtop, jetson-containers, Ollama).

A terminal (TUI) version is still included for power users.

## Download

Direct links (always the newest release):

| Platform | Graphical app | Checksum |
|---|---|---|
| **macOS Apple Silicon** (M1–M4) – installer **.dmg** | [JetsonFlasher-macos-arm64.dmg](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-macos-arm64.dmg) | [SHA-256](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-macos-arm64.dmg.sha256) |
| **macOS Intel** – installer **.dmg** | [JetsonFlasher-macos-x64.dmg](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-macos-x64.dmg) | [SHA-256](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-macos-x64.dmg.sha256) |
| **Windows x64** – wizard (download + SSH tuning) | [JetsonFlasher-windows-x64.exe](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-windows-x64.exe) | [SHA-256](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/JetsonFlasher-windows-x64.exe.sha256) |

Terminal versions: [macOS arm64](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/jetson-flasher-macos-arm64) · [macOS Intel](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/jetson-flasher-macos-x64) · [Windows](https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases/latest/download/jetson-flasher-windows-x64.exe).

- All releases: <https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/releases>
- CI builds (artifacts kept 30 days, GitHub login needed): <https://github.com/fernandoquinino-ntx/jetson-nano-bootstrap/actions/workflows/build.yml>

### Install on macOS (DMG)

1. Download the `.dmg` for your Mac and open it.
2. Drag **Jetson Flasher** onto the **Applications** shortcut.
3. Eject the disk image, open *Applications* → **Jetson Flasher**.
   The app is not signed with an Apple Developer ID, so the first time: **right-click → Open → Open**
   (or run `xattr -dr com.apple.quarantine "/Applications/Jetson Flasher.app"`).
4. When writing a disk, macOS shows its normal administrator-password dialog.

### Install on Windows

Run `JetsonFlasher-windows-x64.exe` (no installation needed). SmartScreen: *More info → Run anyway*.
Disk writing is macOS-only; the Windows app downloads images and runs the SSH firmware / AI tuning
(needs the built-in *OpenSSH Client* feature).

### Run from source

`python3 jetson_flasher_gui.py` (GUI, needs Python 3.9+ with Tk) or `python3 jetson_flasher.py` (terminal). No pip packages.

## The wizard

`Welcome (choose board)` → `What do you want to do?` → then one of:

| Task | Pages |
|---|---|
| Install JetPack 6.2.1 on NVMe / microSD | Image → Disk (type id to confirm) → Write + verify → Next steps |
| JetPack 7.2.1 installer USB stick | Image (ISO) → Disk → Write → Next steps |
| Firmware bridge microSD (JetPack 5.1.3) | Image → Disk → Write → Next steps |
| My NVMe is not detected | Causes + one-click diagnostics (SSH) or bridge microSD |
| Jetson is running → firmware & AI tuning | SSH details + steps → live log, optional reboot |

## What it is for

The Jetson Orin Nano Developer Kit can boot from an NVMe SSD (much faster and larger than microSD), but
NVIDIA's documented route requires SDK Manager on an Ubuntu x86 PC. This tool does it from a Mac:
it writes NVIDIA's official SD-card image directly to the NVMe placed in a USB enclosure.
This works on the Orin Nano dev kit and is widely used, but **it is not NVIDIA's officially documented method**.

| Board | Supported flow |
|---|---|
| Orin Nano 8GB/4GB/Super | JetPack 6.2.1 image → NVMe/SD; JetPack 7.2.1 installer ISO → USB stick; firmware bridge |
| Original Jetson Nano (2019) | JetPack 4.6.1 → microSD/USB only (it has **no** NVMe slot) |

## Quick start (Orin Nano + NVMe)

1. Put the NVMe in a USB enclosure and plug it into the Mac.
2. Run the tool, choose board **1**, then menu **4 → 2** (JetPack 6.2.1 straight to NVMe).
3. Pick the disk, type its id (e.g. `disk4`) to confirm, enter your Mac password. Wait ~10–30 min.
4. Move the NVMe into the Jetson's M.2 Key-M slot, remove any microSD, power on, finish the Ubuntu setup.
5. If you get a black screen / UEFI shell, firmware is too old → menu **6** (see [docs/USAGE.md](docs/USAGE.md#firmware-bridge)).
6. Menu **7** → SSH tuning for AI.

Full walkthrough, every menu option, troubleshooting and safety notes: **[docs/USAGE.md](docs/USAGE.md)**.

## Platform support

| Feature | macOS | Windows | Linux (script) |
|---|---|---|---|
| GUI wizard | ✔ (.dmg) | ✔ (.exe) | `python3 jetson_flasher_gui.py` |
| Check versions, download images | ✔ | ✔ | ✔ |
| List disks / flash / verify | ✔ | ✘ (use balenaEtcher) | ✘ |
| SSH firmware update & AI tuning | ✔ | ✔ (needs OpenSSH client) | ✔ |

## Safety

Flashing **erases the whole target disk**. The tool never lists the disk macOS boots from, shows the disk's
partitions before writing, and requires you to type the disk id. Still, double-check the id and size.

## CI/CD

`.github/workflows/build.yml`: `lint` → `build` → `release`.
- macOS (arm64 + Intel): PyInstaller `.app` → ad-hoc codesign → **DMG** with an *Applications* shortcut; the DMG is
  verified and mounted in CI, and the app is started in a self-test.
- Windows: windowed GUI `.exe` (self-tested) + terminal `.exe`.
- Every file gets a SHA-256. On tag `vX.Y.Z` (or *Actions → build → Run workflow*) a GitHub Release is created.

To release: bump `__version__` in `jetson_flasher.py`, update `CHANGELOG.md`, run the workflow with *release* on
(or `git tag vX.Y.Z && git push --tags`).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
