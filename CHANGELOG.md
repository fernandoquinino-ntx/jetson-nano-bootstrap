# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

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
