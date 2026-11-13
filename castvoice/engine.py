"""Rete: scoperta dei Google Cast, server HTTP locale e riproduzione.

Un Google Nest/Chromecast non riceve l'audio direttamente: gli si dà un URL e
lo scarica lui. Quindi l'app avvia un piccolo server HTTP sul PC (legato
all'interfaccia che raggiunge il dispositivo) che serve l'audio con nomi casuali.
"""
from __future__ import annotations

import functools
import http.server
import logging
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pychromecast
import zeroconf
from pychromecast.discovery import CastBrowser, SimpleCastListener

from . import paths

log = logging.getLogger("castvoice")

GROUP_MODEL = "Google Cast Group"


@dataclass(frozen=True)
class Device:
    uuid: str
    name: str
    model: str
    host: str
    port: int

    @property
    def is_group(self) -> bool:
        return self.model == GROUP_MODEL


def discover(timeout: float = 6.0) -> list[Device]:
    """Cerca i dispositivi Google Cast sulla rete locale (mDNS)."""
    zc = zeroconf.Zeroconf()
    found: dict[str, Device] = {}

    def on_seen(dev_uuid, _service) -> None:
        info = browser.devices.get(dev_uuid)
        if info and info.host:
            found[str(dev_uuid)] = Device(
                uuid=str(dev_uuid),
                name=info.friendly_name or "Senza nome",
                model=info.model_name or "Google Cast",
                host=info.host,
                port=info.port,
            )

    listener = SimpleCastListener(add_callback=on_seen, update_callback=on_seen)
    browser = CastBrowser(listener, zc)
    browser.start_discovery()
    try:
        time.sleep(timeout)
    finally:
        browser.stop_discovery()
        zc.close()
    return sorted(found.values(), key=lambda d: (d.is_group, d.name.lower()))


# --------------------------------------------------------------------------
# Server HTTP locale
# --------------------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # niente rumore sul terminale
        pass


def local_ip_for(host: str) -> str:
    """IP locale dell'interfaccia usata per raggiungere `host`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, 9))
        return s.getsockname()[0]


class LanServer:
    """Un server per ogni IP locale usato; serve solo la cartella temporanea."""

    def __init__(self) -> None:
        self._servers: dict[str, tuple[http.server.ThreadingHTTPServer, int]] = {}
        self._lock = threading.Lock()

    def _server_for(self, ip: str) -> int:
        with self._lock:
            if ip not in self._servers:
                handler = functools.partial(_QuietHandler, directory=str(paths.serve_dir()))
                srv = http.server.ThreadingHTTPServer((ip, 0), handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                self._servers[ip] = (srv, srv.server_address[1])
            return self._servers[ip][1]

    def publish(self, audio: Path, target_host: str) -> str:
        """Copia l'audio nella cartella servita (nome casuale) e dà l'URL."""
        self._cleanup()
        ip = local_ip_for(target_host)
        port = self._server_for(ip)
        name = f"{uuid.uuid4().hex}{audio.suffix}"
        shutil.copyfile(audio, paths.serve_dir() / name)
        return f"http://{ip}:{port}/{name}"

    @staticmethod
    def _cleanup(max_age: float = 3600.0) -> None:
        now = time.time()
        for f in paths.serve_dir().iterdir():
            try:
                if now - f.stat().st_mtime > max_age:
                    f.unlink()
            except OSError:
                pass

    def close(self) -> None:
        for srv, _ in self._servers.values():
            srv.shutdown()
        self._servers.clear()
        shutil.rmtree(paths.serve_dir(), ignore_errors=True)


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
def prepare_audio(src: Path) -> Path:
    """Converte in MP3 (formato che tutti i Google Cast leggono)."""
    if src.suffix.lower() == ".mp3":
        return src
    paths.ensure_dirs()
    out = paths.CACHE_DIR / f"{uuid.uuid4().hex}.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "2", "-b:a", "160k", str(out)],
        check=True,
    )
    return out


def duration_seconds(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.SubprocessError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# Riproduzione
# --------------------------------------------------------------------------
class Player:
    def __init__(self) -> None:
        self._casts: dict[str, pychromecast.Chromecast] = {}
        self._lock = threading.Lock()
        self.server = LanServer()

    def _connect(self, d: Device) -> pychromecast.Chromecast:
        with self._lock:
            cast = self._casts.get(d.uuid)
            if cast is not None and cast.socket_client.is_alive() and cast.socket_client.is_connected:
                return cast
            cast = pychromecast.get_chromecast_from_host(
                (d.host, d.port, uuid.UUID(d.uuid), d.model, d.name)
            )
            cast.wait(timeout=12)
            self._casts[d.uuid] = cast
            return cast

    def _with_retry(self, d: Device, fn):
        try:
            return fn(self._connect(d))
        except Exception:
            self._casts.pop(d.uuid, None)  # riconnette e riprova una volta
            return fn(self._connect(d))

    def get_volume(self, d: Device) -> int:
        return self._with_retry(d, lambda c: round((c.status.volume_level or 0) * 100))

    def set_volume(self, d: Device, percent: int) -> None:
        def go(c):
            c.set_volume_muted(False)
            c.set_volume(max(0, min(100, percent)) / 100)
        self._with_retry(d, go)

    def play(self, d: Device, audio: Path, title: str = "CastVoice") -> None:
        url = self.server.publish(audio, d.host)

        def go(c):
            mc = c.media_controller
            mc.play_media(url, "audio/mpeg", title=title)
            mc.block_until_active(timeout=12)
        self._with_retry(d, go)

    def stop(self, d: Device) -> None:
        def go(c):
            try:
                c.media_controller.stop()
            except Exception:
                pass
            c.quit_app()
        self._with_retry(d, go)

    def play_many(self, devices: list[Device], audio: Path, title: str = "CastVoice") -> list[str]:
        """Riproduce in parallelo. Ritorna la lista degli errori (vuota = tutto ok)."""
        return self._run_many(devices, lambda d: self.play(d, audio, title))

    def stop_many(self, devices: list[Device]) -> list[str]:
        return self._run_many(devices, self.stop)

    @staticmethod
    def _run_many(devices: list[Device], fn) -> list[str]:
        errors: list[str] = []

        def one(d: Device) -> None:
            try:
                fn(d)
            except Exception as exc:  # noqa: BLE001 - mostriamo l'errore all'utente
                log.warning("%s: %s", d.name, exc)
                errors.append(f"{d.name}: {exc}")

        with ThreadPoolExecutor(max_workers=max(1, len(devices))) as ex:
            list(ex.map(one, devices))
        return errors

    def close(self) -> None:
        for cast in self._casts.values():
            try:
                cast.disconnect(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        self._casts.clear()
        self.server.close()
