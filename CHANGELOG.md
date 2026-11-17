# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [2.1.0] - 2026-11-17

### Added
- **Boot over the network (HTTP boot)** task in the wizard (macOS and Windows): serves the JetPack installer ISO
  from this computer so the Jetson can boot it with UEFI "HTTPv4" over Ethernet - no USB stick needed.
  Shows the Boot URL (with copy button and IP picker), step-by-step UEFI instructions and live download progress.
- Built-in single-file HTTP server (GET / HEAD / Range; serves only the chosen file; 404 for anything else).

## [2.0.1] - 2026-11-17

### Fixed
- `[Errno 1] Operation not permitted: '/dev/rdiskN'` when flashing from the macOS app: the helper now falls back to
  Apple's `/bin/dd` if opening the raw disk is refused, for both writing and verifying.
- If macOS still blocks access (privacy / TCC), the wizard explains the fix, opens
  *Privacy & Security → Full Disk Access*, and offers a **Try again** button.

## [2.0.0] - 2026-11-17

### Added
- **Graphical wizard** (`jetson_flasher_gui.py`, tkinter) for macOS and Windows: board → task → image → disk →
  write/verify → next steps, plus SSH firmware/AI tuning with live log and reboot button, and the NVMe-not-detected helper.
- **macOS installer DMG** (`JetsonFlasher-macos-arm64.dmg`, `-x64.dmg`): drag **Jetson Flasher.app** to Applications.
- Windows GUI executable `JetsonFlasher-windows-x64.exe`.
- Privileged flash worker (`--worker-flash`) launched through the macOS administrator-password dialog; writes the
  image directly (no `dd`), supports cancel, and verifies by SHA-256 over the whole disk.
- SSH steps run without a terminal (password via `SSH_ASKPASS`, `sudo -S`), output streamed to the GUI.
- CI builds the apps, verifies/mounts the DMG, self-tests the GUI, adds checksums, and can publish a Release.

### Changed
- `download()` accepts a progress callback; stdout-less (windowed) execution is safe.
- Terminal binaries are still published (`jetson-flasher-*`).

## [1.2.0] - 2026-11-17

### Added
- Menu 8 "NVMe not detected? Diagnose + firmware fix": explains the real causes (the NVMe driver is already in
  NVIDIA's kernel, so the usual culprits are seating, non-NVMe M.2 SATA drive, or old QSPI firmware) and
  chains into the firmware-bridge microSD or the SSH diagnostics.
- New SSH step `--nvme` (PCIe/lspci, lsblk, nvme list, dmesg, driver state, firmware hints).
- Post-flash hints now point to menu 8 when the NVMe is not seen.

## [1.1.0] - 2026-11-17

### Added
- GitHub Actions workflow (`.github/workflows/build.yml`) building standalone binaries with PyInstaller:
  macOS (Apple Silicon and Intel) and Windows (x64), with smoke test and SHA-256 files.
  Tagged releases publish them to a GitHub Release.
- `--version` flag.
- Full documentation: `README.md` and `docs/USAGE.md`.
- Windows support for the non-disk features (check versions, download image, SSH firmware/AI tuning).

### Changed
- Disk listing / flashing now report a clear message instead of aborting when run outside macOS.
- Script is no longer rejected at start-up on Windows/Linux.

### Fixed
- `jetson_postinstall.sh` is always written with LF line endings (CRLF from Windows would break it on the Jetson).
- ANSI colours are enabled on the Windows console.

## [1.0.0]

### Added
- Initial interactive tool (`jetson_flasher.py`): list external disks, check latest JetPack,
  download official image with resume/cache, stream-flash to NVMe/SD/USB without extraction,
  sampled write verification, JetPack 5.1.3 firmware-bridge card, and SSH post-install
  (firmware/QSPI, apt upgrade, JetPack SDK, resize, power mode, swap, headless, Docker,
  jtop/jetson-containers, Ollama).
