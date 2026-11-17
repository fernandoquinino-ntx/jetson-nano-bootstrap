#!/usr/bin/env python3
"""
Jetson Flasher - graphical wizard (macOS and Windows)
=====================================================

Same engine as jetson_flasher.py, with a step-by-step wizard instead of a terminal menu:

    Welcome -> What do you want to do? -> Image -> Disk -> Flash -> Done      (flashing, macOS)
    Welcome -> What do you want to do? -> SSH details -> Run                   (firmware / AI tuning)

Run:  python3 jetson_flasher_gui.py     (or install the .dmg / run the .exe)
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import jetson_flasher as jf

IS_MAC = sys.platform == "darwin"
APP_TITLE = f"Jetson Flasher {jf.__version__}"

TASKS_ORIN = [
    ("flash_image", "Install JetPack 6.2.1 on NVMe / microSD",
     "Writes NVIDIA's official image straight to the disk (recommended for most people)."),
    ("flash_iso", "Make a JetPack 7.2.1 installer USB stick",
     "Newest release. The Jetson boots from the stick and installs onto its NVMe. Needs firmware 36.x."),
    ("http_boot", "Boot the JetPack 7.2.1 installer over the network (HTTP boot)",
     "No USB stick: this computer serves the ISO, the Jetson boots it with UEFI “HTTPv4” over Ethernet."),
    ("bridge", "Firmware bridge: make a JetPack 5.1.3 microSD",
     "For a new Orin Nano with old firmware (UEFI shell / black screen / NVMe not seen)."),
    ("nvme_help", "My NVMe is not detected",
     "Explains the causes and runs diagnostics + firmware update."),
    ("ssh", "Jetson is running: update firmware & tune it for AI (SSH)",
     "CUDA/TensorRT, MAXN power mode, swap, Docker, jtop, Ollama ..."),
]
TASKS_NANO = [
    ("flash_image", "Write JetPack 4.6.1 to a microSD / USB disk",
     "The original Jetson Nano has no NVMe slot."),
    ("ssh", "Jetson is running: tune it over SSH", "Power mode, swap, Docker, tools ..."),
]


# --------------------------------------------------------------------------
# App shell
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x680")
        self.minsize(760, 600)
        self.q: queue.Queue = queue.Queue()
        self.s = dict(board="orin", task=None, image=None, disk=None, verify=True,
                      ssh_preset=None)
        self.pages: list = []
        self.idx = 0

        head = ttk.Frame(self, padding=(20, 14, 20, 6))
        head.pack(fill="x")
        self.h_title = ttk.Label(head, font=("", 17, "bold"))
        self.h_title.pack(anchor="w")
        self.h_sub = ttk.Label(head, foreground="#666")
        self.h_sub.pack(anchor="w")
        ttk.Separator(self).pack(fill="x")

        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill="both", expand=True)

        ttk.Separator(self).pack(fill="x")
        foot = self.foot = ttk.Frame(self, padding=12)
        foot.pack(fill="x")
        self.b_quit = ttk.Button(foot, text="Quit", command=self.on_quit)
        self.b_quit.pack(side="left")
        self.b_next = ttk.Button(foot, text="Next ▸", command=self.go_next)
        self.b_next.pack(side="right")
        self.b_back = ttk.Button(foot, text="◂ Back", command=self.go_back)
        self.b_back.pack(side="right", padx=8)

        self.pages = [WelcomePage(self), TaskPage(self)]
        self.show(0)
        self.after(100, self.pump)

    # thread -> UI bridge
    def ui(self, fn):
        self.q.put(fn)

    def pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.after(100, self.pump)
        except tk.TclError:
            pass

    def show(self, idx):
        self.idx = idx
        for w in self.body.winfo_children():
            w.destroy()
        page = self.pages[idx]
        self.h_title.config(text=page.title)
        self.h_sub.config(text=f"Step {idx + 1} of {len(self.pages)}" if len(self.pages) > 2 else "")
        self.b_back.state(["!disabled"] if idx > 0 else ["disabled"])
        last = idx >= 2 and idx == len(self.pages) - 1
        self.b_next.config(text="Finish" if last else "Next ▸")
        self.b_next.state(["!disabled"])
        page.build(self.body)

    def go_next(self):
        page = self.pages[self.idx]
        if not page.validate():
            return
        if self.idx == len(self.pages) - 1:
            self.destroy()
        else:
            self.show(self.idx + 1)

    def go_back(self):
        if self.idx > 0:
            self.pages[self.idx].leave()
            self.show(self.idx - 1)

    def set_flow(self, task: str):
        """Rebuild the pages after the task page according to the chosen task."""
        self.s["task"] = task
        flow: list = []
        if task == "flash_image":
            flow = [ImagePage(self, "image"), DiskPage(self), FlashPage(self), DonePage(self)]
        elif task == "flash_iso":
            flow = [ImagePage(self, "iso"), DiskPage(self, min_gb=16), FlashPage(self), DonePage(self)]
        elif task == "http_boot":
            flow = [ImagePage(self, "iso"), HttpBootPage(self)]
        elif task == "bridge":
            flow = [ImagePage(self, "bridge"), DiskPage(self, min_gb=32), FlashPage(self), DonePage(self)]
        elif task == "nvme_help":
            flow = [NvmeHelpPage(self)]
        elif task == "ssh":
            flow = [SSHPage(self), SSHRunPage(self)]
        self.pages = self.pages[:2] + flow

    def on_quit(self):
        if getattr(self.pages[self.idx], "busy", False):
            if not messagebox.askyesno("Busy", "An operation is running. Quit anyway?"):
                return
        self.destroy()


class Page:
    title = ""
    busy = False

    def __init__(self, app: App):
        self.app = app

    def build(self, f):  # noqa: D401
        pass

    def validate(self) -> bool:
        return True

    def leave(self):
        pass

    def label(self, f, text, **kw):
        lb = ttk.Label(f, text=text, wraplength=740, justify="left", **kw)
        lb.pack(anchor="w", pady=(0, 8))
        return lb


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

class WelcomePage(Page):
    title = "Welcome"

    def build(self, f):
        self.label(f, "Set up your NVIDIA Jetson for AI in a few clicks.", font=("", 13))
        self.label(f, "This wizard downloads the official JetPack image, writes it to your NVMe / microSD / "
                      "USB disk, helps with old firmware, and then tunes the running Jetson over SSH.")
        self.board = tk.StringVar(value=self.app.s["board"])
        box = ttk.LabelFrame(f, text="Which Jetson do you have?", padding=12)
        box.pack(fill="x", pady=10)
        for key in jf.CATALOG:
            ttk.Radiobutton(box, text=jf.CATALOG[key]["label"], value=key,
                            variable=self.board).pack(anchor="w", pady=2)
        if not IS_MAC:
            warn = ("Windows: downloading images and the SSH firmware / AI tuning work here. Writing disks is "
                    "macOS-only - write the downloaded image with balenaEtcher.")
            self.label(f, warn, foreground="#a60")
        self.label(f, "⚠ Flashing erases the whole target disk. Double-check the disk before confirming.",
                   foreground="#b00")

    def validate(self):
        self.app.s["board"] = self.board.get()
        return True


class TaskPage(Page):
    title = "What do you want to do?"

    def build(self, f):
        tasks = TASKS_ORIN if self.app.s["board"] == "orin" else TASKS_NANO
        self.var = tk.StringVar(value=self.app.s["task"] or tasks[0][0])
        for key, name, desc in tasks:
            fr = ttk.Frame(f)
            fr.pack(fill="x", pady=5)
            ttk.Radiobutton(fr, text=name, value=key, variable=self.var).pack(anchor="w")
            ttk.Label(fr, text=desc, foreground="#666", wraplength=700).pack(anchor="w", padx=24)

    def validate(self):
        self.app.set_flow(self.var.get())
        return True


class ImagePage(Page):
    def __init__(self, app, which):
        super().__init__(app)
        self.which = which
        self.entry = jf.CATALOG[app.s["board"]][which]
        names = {"image": "Get the JetPack image", "iso": "Get the JetPack 7.2.1 installer ISO",
                 "bridge": "Get the JetPack 5.1.3 firmware-bridge image"}
        self.title = names[which]

    def build(self, f):
        e = self.entry
        self.label(f, f"JetPack {e['jetpack']} (L4T {e['l4t']})", font=("", 13, "bold"))
        if e.get("note"):
            self.label(f, e["note"], foreground="#666")
        self.status = tk.StringVar(value="Ready to download (resumable). Files are cached in ~/JetsonImages.")
        ttk.Label(f, textvariable=self.status, wraplength=740).pack(anchor="w", pady=6)
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x", pady=6)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        self.b_dl = ttk.Button(row, text="Download automatically", command=self.start)
        self.b_dl.pack(side="left")
        ttk.Button(row, text="Use a file I already have…", command=self.browse).pack(side="left", padx=8)
        if e.get("page"):
            ttk.Button(row, text="Open NVIDIA page", command=lambda: webbrowser.open(e["page"])).pack(side="left")
        if self.app.s["image"] and Path(self.app.s["image"]).exists():
            self.status.set(f"Selected: {self.app.s['image']}")
            self.bar["value"] = 100

    def browse(self):
        p = filedialog.askopenfilename(title="Choose image",
                                       filetypes=[("Images", "*.zip *.img *.iso"), ("All", "*.*")])
        if p:
            self.app.s["image"] = p
            self.status.set(f"Selected: {p}")
            self.bar["value"] = 100

    def start(self):
        self.busy = True
        self.b_dl.state(["disabled"])
        self.app.b_next.state(["disabled"])
        self.status.set("Downloading…")
        last = [0.0]

        def prog(done, total):
            if time.time() - last[0] < 0.2 and done < total:
                return
            last[0] = time.time()
            pct = done * 100 / total if total else 0
            self.app.ui(lambda: (self.bar.config(value=pct),
                                 self.status.set(f"Downloading… {jf.human(done)} / {jf.human(total)}")))

        def work():
            path = jf.download(self.entry["url"], on_progress=prog)

            def fin():
                self.busy = False
                self.b_dl.state(["!disabled"])
                self.app.b_next.state(["!disabled"])
                if path:
                    self.app.s["image"] = str(path)
                    self.bar.config(value=100)
                    self.status.set(f"Ready: {path}")
                else:
                    self.status.set("Download failed. Run it again to resume, or download in your browser "
                                    "and choose “Use a file I already have”.")
            self.app.ui(fin)

        threading.Thread(target=work, daemon=True).start()

    def validate(self):
        if self.busy:
            return False
        p = self.app.s["image"]
        if not p or not Path(p).exists():
            messagebox.showwarning("Image needed", "Download the image or choose a local file first.")
            return False
        return True


class DiskPage(Page):
    title = "Choose the disk to ERASE"

    def __init__(self, app, min_gb=None):
        super().__init__(app)
        self.min_gb = min_gb or jf.CATALOG[app.s["board"]]["min_size_gb"]
        self.disks: list = []

    def build(self, f):
        if not IS_MAC:
            self.label(f, "Writing disks from this app is only supported on macOS.", foreground="#a60")
            self.label(f, "Write the image you just downloaded with balenaEtcher (https://etcher.balena.io) "
                          "or Rufus, then continue.")
            self.label(f, f"Image: {self.app.s['image']}")
            return
        self.label(f, "Plug in the NVMe enclosure / SD card / USB stick, then pick it. Your Mac's own disk "
                      "is never shown.")
        cols = ("id", "size", "type", "name")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w in zip(cols, (80, 100, 100, 420)):
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=w)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        ttk.Button(row, text="Refresh", command=self.refresh).pack(side="left")
        self.info = tk.StringVar()
        ttk.Label(f, textvariable=self.info, wraplength=740, foreground="#666").pack(anchor="w")
        box = ttk.LabelFrame(f, text="Safety confirmation", padding=10)
        box.pack(fill="x", pady=8)
        ttk.Label(box, text="Type the disk id (for example disk4) to confirm that everything on it will be "
                            "destroyed:", wraplength=720).pack(anchor="w")
        self.confirm = tk.StringVar()
        ttk.Entry(box, textvariable=self.confirm, width=16).pack(anchor="w", pady=4)
        self.verify = tk.BooleanVar(value=self.app.s["verify"])
        ttk.Checkbutton(f, text="Verify the write afterwards (reads the whole disk back; recommended)",
                        variable=self.verify).pack(anchor="w")
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.info.set("Scanning…")

        def work():
            try:
                disks = jf.list_external_disks()
            except Exception as e:  # noqa: BLE001
                self.app.ui(lambda: self.info.set(f"Could not list disks: {e}"))
                return

            def fin():
                self.disks = disks
                for d in disks:
                    self.tree.insert("", "end", iid=d.ident,
                                     values=(d.ident, jf.human(d.size) if d.size else "no media", d.kind,
                                             f"{d.name} [{d.protocol}]"))
                self.info.set("" if disks else "No external disk or SD card found. Plug it in and press Refresh.")
            self.app.ui(fin)
        threading.Thread(target=work, daemon=True).start()

    def on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        d = next(x for x in self.disks if x.ident == sel[0])
        parts = "; ".join(d.partitions) or "no partitions"
        self.info.set(f"{d.ident}: {parts}")

    def validate(self):
        if not IS_MAC:
            return True
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Choose a disk", "Select the target disk in the list.")
            return False
        d = next(x for x in self.disks if x.ident == sel[0])
        if not d.size:
            messagebox.showerror("No media", "That slot has no card / media inserted.")
            return False
        if d.size < self.min_gb * 1e9 * 0.93 and not messagebox.askyesno(
                "Small disk", f"{d.ident} is {jf.human(d.size)}; at least {self.min_gb} GB is recommended.\n"
                              "Continue anyway?"):
            return False
        if self.confirm.get().strip() != d.ident:
            messagebox.showwarning("Confirmation", f"Type exactly “{d.ident}” to confirm.")
            return False
        self.app.s["disk"] = d
        self.app.s["verify"] = self.verify.get()
        return messagebox.askyesno(
            "Erase disk?", f"ERASE {d.ident} ({d.name}, {jf.human(d.size)}) and write\n"
                           f"{Path(self.app.s['image']).name}?\n\nThis cannot be undone.", icon="warning")


class FlashPage(Page):
    title = "Writing the disk"

    def build(self, f):
        s = self.app.s
        if not IS_MAC:
            self.label(f, "Nothing to do on this OS - write the image with balenaEtcher.")
            return
        d = s["disk"]
        self.label(f, f"Writing {Path(s['image']).name} to {d.ident} ({d.name}). macOS will ask for your "
                      "administrator password. Do not unplug the disk.")
        self.status = tk.StringVar(value="Waiting for administrator approval…")
        ttk.Label(f, textvariable=self.status, wraplength=740).pack(anchor="w", pady=6)
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x", pady=6)
        row = ttk.Frame(f)
        row.pack(anchor="w")
        self.b_cancel = ttk.Button(row, text="Cancel", command=self.cancel)
        self.b_cancel.pack(side="left")
        self.b_retry = ttk.Button(row, text="Try again", command=self.start)
        self.b_retry.pack(side="left", padx=8)
        self.b_retry.state(["disabled"])
        self.ok = False
        self.start()

    def start(self):
        self.busy = True
        self.b_retry.state(["disabled"])
        self.b_cancel.state(["!disabled"])
        self.bar.config(value=0)
        self.status.set("Waiting for administrator approval…")
        self.app.b_next.state(["disabled"])
        self.app.b_back.state(["disabled"])
        threading.Thread(target=self.run_worker, daemon=True).start()

    def cancel(self):
        Path(self.sfile + ".cancel").write_text("1")
        self.status.set("Cancelling…")

    def run_worker(self):
        s = self.app.s
        fd, self.sfile = tempfile.mkstemp(prefix="jetsonflash-", suffix=".log")
        os.close(fd)
        os.chmod(self.sfile, 0o666)
        me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
        args = me + ["--worker-flash", s["image"], s["disk"].ident, self.sfile, "1" if s["verify"] else "0"]
        cmd = " ".join(shlex.quote(a) for a in args)
        script = 'do shell script "%s" with administrator privileges' % cmd.replace("\\", "\\\\").replace('"', '\\"')
        proc = subprocess.Popen(["osascript", "-e", script], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        pos, final = 0, None
        while True:
            rc = proc.poll()
            try:
                with open(self.sfile) as fh:
                    fh.seek(pos)
                    for line in fh.read().splitlines():
                        ev = json.loads(line)
                        if ev["phase"] in ("write", "verify"):
                            self.app.ui(lambda ev=ev: self.progress(ev))
                        else:
                            final = ev
                    pos = fh.tell()
            except (OSError, ValueError):
                pass
            if final is not None or rc is not None:
                break
            time.sleep(0.3)
        err = (proc.stderr.read() if proc.poll() is not None else "") or ""
        self.app.ui(lambda: self.finished(final, err, proc.returncode))

    def progress(self, ev):
        if not hasattr(self, "bar") or not self.bar.winfo_exists():
            return
        pct = ev["done"] * 100 / max(ev["total"], 1)
        verb = "Writing" if ev["phase"] == "write" else "Verifying"
        self.bar.config(value=pct)
        self.status.set(f"{verb}… {jf.human(ev['done'])} / {jf.human(ev['total'])}")

    def finished(self, final, err, rc):
        self.busy = False
        self.app.b_back.state(["!disabled"])
        if not self.bar.winfo_exists():
            return
        if final and final["phase"] == "done":
            self.ok = True
            self.bar.config(value=100)
            self.status.set("✔ Finished" + (" and verified." if final.get("verified") else "."))
            self.b_cancel.state(["disabled"])
            self.app.b_next.state(["!disabled"])
        else:
            msg = (final or {}).get("msg") or err.strip() or f"The write did not start (exit code {rc})."
            if "User canceled" in msg or "-128" in msg:
                msg = "Administrator approval was cancelled."
            self.status.set("✖ " + msg)
            self.b_retry.state(["!disabled"])
            if (final or {}).get("code") == "eperm" or "not permitted" in msg.lower():
                if messagebox.askyesno(
                        "macOS blocked access to the disk",
                        f"{msg}\n\nmacOS privacy protection is blocking raw disk access for this app.\n\n"
                        "Fix: System Settings → Privacy & Security → Full Disk Access → turn ON “Jetson Flasher” "
                        "(use + to add it from Applications if it is missing), quit and reopen the app, "
                        "and try again.\n\nOpen that settings page now?"):
                    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?"
                                            "Privacy_AllFiles"], check=False)
            else:
                messagebox.showerror("Flash failed", msg)

    def validate(self):
        return (not IS_MAC) or self.ok


class DonePage(Page):
    title = "All done"

    def build(self, f):
        t, s = self.app.s["task"], self.app.s
        if t == "flash_iso":
            steps = ["Firmware must already be 36.x (new unit? run “Firmware bridge” first).",
                     "Install the NVMe in the M.2 Key-M slot (bottom of the carrier board).",
                     "Plug the USB stick into the Jetson, connect monitor + keyboard, power on.",
                     "Press Esc at the NVIDIA logo → Boot Manager → choose the USB stick.",
                     "In the installer pick the NVMe as the target. Remove the stick when it finishes.",
                     "NVMe not listed? Restart this wizard → “My NVMe is not detected”."]
        elif t == "bridge":
            steps = ["Put the microSD in the slot under the Jetson module and power on.",
                     "Finish setup, reboot once (firmware → 35.5.0).",
                     "Run this wizard → “Jetson is running…” (SSH) with the firmware step to move to 36.x.",
                     "Power off, remove the microSD, boot your JetPack 6 NVMe / USB installer."]
        else:
            disk = s.get("disk")
            kind = disk.kind if disk else ""
            steps = (["Put the microSD card in the slot under the Jetson module."] if kind == "SD card" else
                     ["Install the NVMe in the M.2 Key-M slot (remove the microSD so it boots from NVMe)."]) + [
                "Power on with monitor + keyboard and finish the Ubuntu first-boot setup.",
                "Black screen / UEFI shell / NVMe not seen? The firmware is too old → run the wizard again "
                "and choose “My NVMe is not detected”.",
                "When it boots, run this wizard → “Jetson is running…” to update firmware and tune it for AI."]
        self.label(f, "Next steps", font=("", 13, "bold"))
        for i, st in enumerate(steps, 1):
            self.label(f, f"{i}. {st}")


class HttpBootPage(Page):
    title = "Boot the Jetson over HTTP"

    def build(self, f):
        self.server = None
        img = Path(self.app.s["image"])
        self.label(f, f"This computer will serve  {img.name}  ({jf.human(img.stat().st_size)}) to the Jetson. "
                      "Connect the Jetson to the same network with an Ethernet cable (UEFI cannot use Wi-Fi).")
        if " " in img.name:
            self.label(f, "⚠ The file name contains spaces; rename it (no spaces) for best UEFI compatibility.",
                       foreground="#a60")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="This computer's IP").pack(side="left")
        ips = jf.lan_ips()
        self.ip = tk.StringVar(value=ips[0])
        ttk.Combobox(row, textvariable=self.ip, values=ips, width=18).pack(side="left", padx=6)
        ttk.Label(row, text="Port").pack(side="left", padx=(12, 0))
        self.port = tk.StringVar(value="8000")
        ttk.Entry(row, textvariable=self.port, width=6).pack(side="left", padx=6)
        self.b_start = ttk.Button(row, text="Start server", command=self.toggle)
        self.b_start.pack(side="left", padx=12)

        self.url = tk.StringVar()
        urow = ttk.Frame(f)
        urow.pack(fill="x", pady=4)
        ttk.Label(urow, text="Boot URL").pack(side="left")
        ttk.Entry(urow, textvariable=self.url, state="readonly", width=70).pack(side="left", padx=6)
        ttk.Button(urow, text="Copy", command=self.copy).pack(side="left")

        box = ttk.LabelFrame(f, text="On the Jetson", padding=8)
        box.pack(fill="x", pady=6)
        for t in ("1. Power on and press Esc at the NVIDIA logo to open the UEFI menu.",
                  "2. Device Manager → Network Device List → your network card → HTTP Boot Configuration.",
                  "3. Set “Boot URI” to the Boot URL above and save (no DHCP? set IPv4 under IPv4 Network Configuration).",
                  "4. Back to Boot Manager → choose “UEFI HTTPv4 (MAC:…)”. The ISO downloads into RAM and the installer starts.",
                  "Needs firmware 36.x and enough RAM for the ISO (8 GB module recommended). Menu names can vary."):
            ttk.Label(box, text=t, wraplength=760, justify="left").pack(anchor="w")
        self.status = tk.StringVar(value="Server stopped.")
        ttk.Label(f, textvariable=self.status, wraplength=780).pack(anchor="w", pady=(6, 2))
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x")
        self.log = tk.Text(f, height=5, state="disabled", font=("Menlo" if IS_MAC else "Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=6)
        self.ip.trace_add("write", lambda *_: self.url_for_ip())
        self.port.trace_add("write", lambda *_: self.url_for_ip())
        self.url_for_ip()

    def url_for_ip(self):
        self.url.set(f"http://{self.ip.get()}:{self.port.get()}/{Path(self.app.s['image']).name}")

    def copy(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.url.get())

    def write(self, line):
        def f():
            if self.log.winfo_exists():
                self.log.config(state="normal")
                self.log.insert("end", time.strftime("%H:%M:%S ") + line + "\n")
                self.log.see("end")
                self.log.config(state="disabled")
        self.app.ui(f)

    def event(self, kind, a=None, b=None, c=None):
        if kind == "progress":
            pct = b * 100 / max(c, 1)
            self.app.ui(lambda: (self.bar.config(value=pct) if self.bar.winfo_exists() else None,
                                 self.status.set(f"{a} is downloading… {jf.human(b)} / {jf.human(c)}")
                                 if self.bar.winfo_exists() else None))
        elif kind == "head":
            self.write(f"{a} asked for the file size (HEAD)")
        elif kind == "done":
            self.write(f"{a} downloaded the complete file ✔")
            self.app.ui(lambda: self.status.set(f"{a} finished downloading. The installer should start now."))
        elif kind == "aborted":
            self.write(f"{a} disconnected at {jf.human(b)}")
        elif kind == "miss":
            self.write(f"{a} requested a wrong path: {b}  (check the Boot URI)")

    def toggle(self):
        if self.server:
            self.stop()
            return
        if not self.port.get().isdigit():
            messagebox.showwarning("Port", "Enter a numeric port, e.g. 8000.")
            return
        try:
            self.server = jf.SingleFileServer(self.app.s["image"], int(self.port.get()), self.event)
            self.server.start()
        except OSError as e:
            self.server = None
            messagebox.showerror("Cannot start server", f"{e}\n\nTry another port.")
            return
        self.busy = True
        self.url_for_ip()
        self.b_start.config(text="Stop server")
        self.status.set(f"Serving on port {self.server.port}. Waiting for the Jetson…")
        self.write(f"Server started: {self.url.get()}")
        self.write("If a firewall prompt appears, allow incoming connections (private networks).")

    def stop(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.busy = False
        self.b_start.config(text="Start server")
        self.status.set("Server stopped.")
        self.write("Server stopped.")

    def leave(self):
        self.stop()

    def validate(self):
        self.stop()
        return True


class NvmeHelpPage(Page):
    title = "NVMe not detected"

    def build(self, f):
        self.label(f, "The NVMe driver is already inside NVIDIA's Linux kernel - there is nothing to install "
                      "on the USB stick. If no NVMe shows up, it is almost always one of these:")
        for t in ("1. The SSD is not fully seated / screwed down in the M.2 Key-M slot (underside of the board).",
                  "2. The SSD is M.2 SATA - only PCIe NVMe drives work.",
                  "3. The QSPI firmware is older than 36.x (very common on a new Orin Nano).",
                  "4. A few SSDs fail PCIe link training - try another drive."):
            self.label(f, t)
        self.label(f, "Recommended: re-seat the SSD first. If it is still missing, boot the JetPack 5.1.3 "
                      "firmware-bridge microSD and run the diagnostics + firmware update.")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=10)
        ttk.Button(row, text="Make the firmware-bridge microSD", command=lambda: self.jump("bridge")).pack(side="left")
        ttk.Button(row, text="Run diagnostics on my Jetson (SSH)",
                   command=lambda: self.jump("ssh", ["--info", "--nvme", "--firmware"])).pack(side="left", padx=8)

    def jump(self, task, preset=None):
        self.app.s["ssh_preset"] = preset
        self.app.set_flow(task)
        self.app.show(2)


class SSHPage(Page):
    title = "Connect to your Jetson"

    def build(self, f):
        self.label(f, "The Jetson must be booted and on your network (or connected by USB-C: 192.168.55.1) "
                      "with SSH enabled (finish the first-boot setup first).")
        g = ttk.Frame(f)
        g.pack(anchor="w")
        self.host, self.user, self.pw = tk.StringVar(value="192.168.55.1"), tk.StringVar(value="nvidia"), tk.StringVar()
        for r, (lbl, var, show) in enumerate((("Jetson IP / hostname", self.host, ""),
                                              ("Username", self.user, ""), ("Password", self.pw, "•"))):
            ttk.Label(g, text=lbl).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            ttk.Entry(g, textvariable=var, width=30, show=show).grid(row=r, column=1, pady=3)
        box = ttk.LabelFrame(f, text="What should be done?", padding=8)
        box.pack(fill="x", pady=8)
        preset = self.app.s.get("ssh_preset")
        self.vars = {}
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for i, (flag, label, default) in enumerate(jf.REMOTE_STEPS):
            v = tk.BooleanVar(value=(flag in preset) if preset else default)
            self.vars[flag] = v
            ttk.Checkbutton(grid, text=label, variable=v).grid(row=i, column=0, sticky="w")
        row = ttk.Frame(f)
        row.pack(anchor="w")
        ttk.Label(row, text="Swapfile GB (if swap selected)").pack(side="left")
        self.swap = tk.StringVar(value="16")
        ttk.Entry(row, textvariable=self.swap, width=5).pack(side="left", padx=6)
        ttk.Label(row, text="Move APT repo to L4T (JetPack 6 only, e.g. 36.5; empty = keep)").pack(side="left", padx=(12, 0))
        self.up = tk.StringVar()
        ttk.Entry(row, textvariable=self.up, width=7).pack(side="left", padx=6)

    def validate(self):
        if not self.host.get().strip() or not self.user.get().strip() or not self.pw.get():
            messagebox.showwarning("Missing data", "Enter the IP/hostname, username and password.")
            return False
        steps = [fl for fl, v in self.vars.items() if v.get()]
        if not steps:
            messagebox.showwarning("Nothing selected", "Select at least one step.")
            return False
        env = []
        if "--swap" in steps:
            env.append(f"SWAP_GB={int(self.swap.get()) if self.swap.get().isdigit() else 16}")
        if "--upgrade" in steps and self.up.get().strip():
            env.append(f"UPGRADE_TO={self.up.get().strip()}")
        self.app.s["ssh"] = dict(host=self.host.get().strip(), user=self.user.get().strip(),
                                 pw=self.pw.get(), steps=steps, env=env)
        return True


class SSHRunPage(Page):
    title = "Running on the Jetson"

    def build(self, f):
        self.cfg = self.app.s["ssh"]
        self.log = tk.Text(f, height=20, wrap="word", state="disabled", font=("Menlo" if IS_MAC else "Consolas", 10))
        sb = ttk.Scrollbar(f, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.b_reboot = ttk.Button(self.app.foot, text="Reboot Jetson",
                                   command=self.reboot)
        self.b_reboot.pack(side="left", padx=8)
        self.b_reboot.state(["disabled"])
        self.busy = True
        self.app.b_next.state(["disabled"])
        self.app.b_back.state(["disabled"])
        threading.Thread(target=self.work, daemon=True).start()

    def leave(self):
        pass

    def write(self, line: str):
        def f():
            if self.log.winfo_exists():
                self.log.config(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.config(state="disabled")
        self.app.ui(f)

    def work(self):
        c = self.cfg
        rc = jf.run_remote(c["host"], c["user"], c["pw"], c["steps"], c["env"], self.write)

        def fin():
            self.busy = False
            self.write("\n✔ Finished." if rc == 0 else f"\n✖ Finished with errors (exit code {rc}).")
            self.app.b_next.state(["!disabled"])
            self.app.b_back.state(["!disabled"])
            if self.b_reboot.winfo_exists():
                self.b_reboot.state(["!disabled"])
        self.app.ui(fin)

    def reboot(self):
        if messagebox.askyesno("Reboot", "Reboot the Jetson now?"):
            c = self.cfg
            threading.Thread(target=lambda: jf.run_remote(c["host"], c["user"], c["pw"], [], [], self.write,
                                                          reboot=True), daemon=True).start()


# --------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print(f"jetson-flasher-gui {jf.__version__}")
        return
    if len(argv) >= 5 and argv[0] == "--worker-flash":      # privileged helper
        sys.exit(jf.worker_flash(argv[1], argv[2], argv[3], argv[4] == "1"))
    if argv[:1] == ["--selftest"]:                         # CI smoke test: Tk works, window builds
        out = argv[1] if len(argv) > 1 else "selftest.txt"
        app = App()
        app.update()
        app.destroy()
        Path(out).write_text(f"ok {jf.__version__}\n")
        return
    App().mainloop()


if __name__ == "__main__":
    main()
