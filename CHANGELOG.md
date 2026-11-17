# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [1.1.0] - 2026-11-17

### Added
- GitLab CI/CD pipeline (`.gitlab-ci.yml`) building standalone binaries with PyInstaller:
  macOS (Apple Silicon) and Windows (x64). Tagged releases publish them to the Package Registry
  and create a GitLab Release.
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
