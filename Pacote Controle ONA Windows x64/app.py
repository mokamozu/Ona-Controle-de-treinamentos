"""Abre o sistema como programa de desktop: servidor interno + janela própria."""
import msvcrt
import ctypes
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
import webview

from main import app


LOCK_PATH = os.path.join(tempfile.gettempdir(), "controle_treinamentos_sos.lock")


def _bloquear_instancia_unica():
    lock = open(LOCK_PATH, "a+b")
    lock.seek(0, os.SEEK_END)
    if lock.tell() == 0:
        lock.write(b"\0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return False
    return lock


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    lock = _bloquear_instancia_unica()
    if not lock:
        return

    try:
        porta = porta_livre()
        cfg = uvicorn.Config(app, host="127.0.0.1", port=porta, log_config=None,
                             loop="asyncio", http="h11", lifespan="off")
        srv = uvicorn.Server(cfg)
        threading.Thread(target=srv.run, daemon=True).start()
        url = f"http://127.0.0.1:{porta}"
        for _ in range(150):
            try:
                with urllib.request.urlopen(url + "/api/dashboard", timeout=1):
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            raise RuntimeError("O servidor local não iniciou a tempo.")
        webview.create_window("Controle de Treinamentos - SOS Emergências Médicas | ONA", url,
                              width=1360, height=860, min_size=(1000, 650))
        try:
            webview.start(gui="edgechromium")
        except webview.WebViewException as exc:
            message = (
                "Não foi possível abrir a janela do aplicativo.\n\n"
                "Instale o Microsoft Edge WebView2 Runtime e tente novamente:\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                f"Detalhe: {exc}"
            )
            ctypes.windll.user32.MessageBoxW(
                None, message, "Controle de Treinamentos SOS", 0x10
            )
        srv.should_exit = True
    finally:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


if __name__ == "__main__":
    main()
