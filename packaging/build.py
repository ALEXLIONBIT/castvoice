#!/usr/bin/env python3
"""Impacchetta CastVoice per il sistema su cui gira, in un file da aprire con doppio clic.

  Linux   → CastVoice-<versione>-x86_64.AppImage
  Windows → CastVoice-<versione>-windows-x64.zip   (dentro c'è CastVoice.exe)
  macOS   → CastVoice-<versione>-macos-<arch>.zip  (dentro c'è CastVoice.app)

Dentro l'app c'è già ffmpeg, quindi non serve installare altro. Uso:

  pip install -r requirements.txt pyinstaller pillow
  python packaging/build.py

Il risultato è in dist-release/. I dati personali (voce, campioni) NON entrano mai nel pacchetto.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build-pkg"
OUT = ROOT / "dist-release"
WIN, MAC = sys.platform == "win32", sys.platform == "darwin"
SEP = ";" if WIN else ":"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "castvoice" / "__init__.py").read_text()).group(1)


def download(url: str, dest: Path) -> Path:
    print(f"→ scarico {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "castvoice-build"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def fetch_ffmpeg() -> Path:
    """Scarica ffmpeg/ffprobe (e ffplay dove c'è) statici in build-pkg/bin. Ritorna la cartella."""
    binp = BUILD / "bin"
    shutil.rmtree(binp, ignore_errors=True)
    binp.mkdir(parents=True)
    tmp = BUILD / "dl"
    if WIN:
        z = download("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip", tmp / "ff.zip")
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if re.search(r"/bin/(ffmpeg|ffprobe|ffplay)\.exe$", n):
                    (binp / Path(n).name).write_bytes(zf.read(n))
    elif MAC:
        for tool in ("ffmpeg", "ffprobe", "ffplay"):
            url = "https://evermeet.cx/ffmpeg/getrelease/zip" if tool == "ffmpeg" else f"https://evermeet.cx/ffmpeg/getrelease/{tool}/zip"
            z = download(url, tmp / f"{tool}.zip")
            with zipfile.ZipFile(z) as zf:
                zf.extract(tool, binp)
    else:
        t = download("https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz", tmp / "ff.tar.xz")
        with tarfile.open(t) as tf:
            for m in tf.getmembers():
                if Path(m.name).name in ("ffmpeg", "ffprobe") and m.isfile():
                    m.name = Path(m.name).name
                    tf.extract(m, binp)
    for f in binp.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("   ffmpeg incluso:", ", ".join(sorted(p.name for p in binp.iterdir())), flush=True)
    return binp


def run_pyinstaller(binp: Path) -> Path:
    shutil.rmtree(BUILD / "pyi", ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "CastVoice",
        "--distpath", str(BUILD / "pyi" / "dist"), "--workpath", str(BUILD / "pyi" / "work"),
        "--specpath", str(BUILD / "pyi"),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'assets'}{SEP}assets",
        "--add-data", f"{ROOT / 'castvoice' / 'qwen_worker.py'}{SEP}castvoice",
        "--add-data", f"{ROOT / 'castvoice' / 'clone_worker.py'}{SEP}castvoice",
        "--add-binary", f"{binp}{os.sep}*{SEP}bin",
        "--collect-all", "pychromecast", "--collect-all", "zeroconf", "--collect-all", "edge_tts",
        "--collect-submodules", "aiohttp", "--collect-data", "certifi",
    ]
    icons = ROOT / "assets" / "icons"
    if WIN:
        cmd += ["--icon", str(icons / "256x256.png")]
    elif MAC:
        cmd += ["--icon", str(icons / "512x512.png"), "--osx-bundle-identifier", "app.castvoice"]
    cmd.append(str(ROOT / "packaging" / "run_castvoice.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)
    return BUILD / "pyi" / "dist"


def selftest(cmd: list[str]) -> None:
    """Avvia l'app impacchettata con --selftest: deve costruirsi, trovare ffmpeg e i file inclusi."""
    result = BUILD / "selftest-result.txt"
    result.unlink(missing_ok=True)
    env = {**os.environ, "CASTVOICE_DATA": str(BUILD / "selftest-data"), "CASTVOICE_SELFTEST_FILE": str(result)}
    env.setdefault("QT_QPA_PLATFORM", "offscreen")  # prova senza finestra, valida anche sui server di build
    r = subprocess.run(cmd + ["--selftest"], env=env, capture_output=True, text=True, timeout=240)
    report = result.read_text(encoding="utf-8") if result.exists() else "(nessun risultato scritto)"
    print(report, "\n", (r.stderr or "")[-1500:])
    if r.returncode != 0 or "SELFTEST OK" not in report:
        sys.exit(f"Prova dell'app impacchettata FALLITA (codice {r.returncode})")


def make_appimage(dist: Path) -> Path:
    app = BUILD / "AppDir"
    shutil.rmtree(app, ignore_errors=True)
    (app / "usr" / "lib").mkdir(parents=True)
    shutil.copytree(dist / "CastVoice", app / "usr" / "lib" / "CastVoice", symlinks=True)
    shutil.copy(ROOT / "assets" / "icons" / "256x256.png", app / "castvoice.png")
    (app / "castvoice.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=CastVoice\nComment=Fai parlare i Google Nest\n"
        "Exec=CastVoice\nIcon=castvoice\nCategories=AudioVideo;Audio;\nTerminal=false\n")
    run = app / "AppRun"
    run.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/lib/CastVoice/CastVoice" "$@"\n')
    run.chmod(0o755)
    tool = download("https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage",
                    BUILD / "appimagetool")
    tool.chmod(0o755)
    out = OUT / f"CastVoice-{VERSION}-x86_64.AppImage"
    OUT.mkdir(exist_ok=True)
    subprocess.run([str(tool), "--appimage-extract-and-run", str(app), str(out)], check=True, env={**os.environ, "ARCH": "x86_64"})
    out.chmod(0o755)
    return out


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # la console di Windows (cp1252) non sa scrivere «→», «·»…
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    BUILD.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    binp = fetch_ffmpeg()
    dist = run_pyinstaller(binp)
    if WIN:
        selftest([str(dist / "CastVoice" / "CastVoice.exe")])
        out = OUT / f"CastVoice-{VERSION}-windows-x64.zip"
        shutil.make_archive(str(out.with_suffix("")), "zip", dist, "CastVoice")
    elif MAC:
        exe = dist / "CastVoice.app" / "Contents" / "MacOS" / "CastVoice"
        selftest([str(exe)])
        arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
        out = OUT / f"CastVoice-{VERSION}-macos-{arch}.zip"
        out.unlink(missing_ok=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(dist / "CastVoice.app"), str(out)], check=True)
    else:
        selftest([str(dist / "CastVoice" / "CastVoice")])
        out = make_appimage(dist)
    print(f"\nFATTO: {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
