# Jetson Bootstrap – NVMe flasher & AI tuner

An interactive, dependency-free (Python standard library only) tool that takes a **NVIDIA Jetson Orin Nano**
(or original Jetson Nano) from "box" to "ready for AI" using nothing but your Mac:

1. finds the external disk (NVMe in a USB enclosure, SD card, USB stick),
2. downloads the official JetPack image (resumable, cached in `~/JetsonImages`),
3. streams it to the disk – no 20 GB extraction – and verifies the write,
4. prepares a **firmware bridge** microSD (JetPack 5.1.3) for units with old QSPI firmware,
5. once the Jetson is booted, connects over **SSH** to update firmware and tune it for AI
   (CUDA/TensorRT, MAXN power mode, swap, Docker + NVIDIA runtime, jtop, jetson-containers, Ollama).

## Download

Binaries are built by GitLab CI/CD. Replace `<GROUP>/<PROJECT>` with this project's GitLab path.

| Platform | Latest build (default branch) | Tagged releases |
|---|---|---|
| macOS (Apple Silicon) – full features | [jetson-flasher-macos-arm64](https://gitlab.com/<GROUP>/<PROJECT>/-/jobs/artifacts/main/raw/dist/jetson-flasher-macos-arm64?job=build:macos) | [Releases](https://gitlab.com/<GROUP>/<PROJECT>/-/releases) |
| Windows (x64) – download + SSH tuning only | [jetson-flasher-windows-x64.exe](https://gitlab.com/<GROUP>/<PROJECT>/-/jobs/artifacts/main/raw/dist/jetson-flasher-windows-x64.exe?job=build:windows) | [Releases](https://gitlab.com/<GROUP>/<PROJECT>/-/releases) |

Or run the script directly: `python3 jetson_flasher.py` (Python 3.9+, no pip packages).

> **Unsigned binaries.** macOS: `chmod +x jetson-flasher-macos-arm64 && xattr -d com.apple.quarantine jetson-flasher-macos-arm64`
> (or right-click → Open). Windows SmartScreen: *More info → Run anyway*.
> Verify with the published `.sha256` file. Intel Macs: run the Python script instead.

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
| Check versions, download images | ✔ | ✔ | ✔ |
| List disks / flash / verify | ✔ | ✘ (use balenaEtcher) | ✘ |
| SSH firmware update & AI tuning | ✔ | ✔ (needs OpenSSH client) | ✔ |

## Safety

Flashing **erases the whole target disk**. The tool never lists the disk macOS boots from, shows the disk's
partitions before writing, and requires you to type the disk id. Still, double-check the id and size.

## CI/CD

`.gitlab-ci.yml` stages: `lint` → `build` (macOS arm64 + Windows x64, PyInstaller `--onefile`) → `release`
(on tag `vX.Y.Z`: uploads to the Package Registry and creates a Release). To release: bump `__version__` in
`jetson_flasher.py`, update `CHANGELOG.md`, then `git tag vX.Y.Z && git push --tags`. The tag must match
`__version__` or the lint job fails. Jobs use GitLab.com SaaS runner tags (`saas-macos-medium-m1`,
`saas-windows-medium-amd64`); on self-managed GitLab, substitute your own runner tags.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
