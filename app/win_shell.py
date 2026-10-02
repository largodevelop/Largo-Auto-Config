"""Windows shell helpers (tray-only UI / single-instance)."""

from __future__ import annotations

import ctypes
import socket
import sys
import threading
from collections.abc import Callable

GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SW_HIDE = 0
SW_RESTORE = 9
SW_SHOW = 5
ERROR_ALREADY_EXISTS = 183

MUTEX_NAME = "Local\\LargoAutoConfig.SingleInstance"
IPC_HOST = "127.0.0.1"
IPC_PORT = 47321
WINDOW_TITLE = "Largo Auto Config"
APP_USER_MODEL_ID = "Largo.AutoConfig"

_MUTEX = None
_IPC_SOCK: socket.socket | None = None
_WATCH_STOP: threading.Event | None = None


def _hwnd_from_tk(root) -> int:
    root.update_idletasks()
    wid = int(root.winfo_id())
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(wid)
    return int(hwnd or wid)


def set_app_id(app_user_model_id: str = APP_USER_MODEL_ID) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_user_model_id)
    except (OSError, AttributeError):
        pass


def hide_from_taskbar(root) -> None:
    """Keep frameless window off the taskbar (tray-only presence)."""
    if sys.platform != "win32":
        return
    set_app_id()
    try:
        hwnd = _hwnd_from_tk(root)
        user32 = ctypes.windll.user32
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        style = get_long(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except (OSError, AttributeError, ctypes.ArgumentError):
        pass


# Back-compat alias used by older calls
def enable_taskbar_and_alt_tab(root, app_user_model_id: str = APP_USER_MODEL_ID) -> None:
    hide_from_taskbar(root)


def _signal_primary_via_socket() -> bool:
    try:
        with socket.create_connection((IPC_HOST, IPC_PORT), timeout=0.8) as conn:
            conn.sendall(b"SHOW\n")
        return True
    except OSError:
        return False


def claim_single_instance() -> bool:
    """
    True = this process is the primary instance.
    False = another copy is running (and was asked to show its window).
    """
    global _MUTEX, _IPC_SOCK
    set_app_id()

    # 1) Socket lock — reliable across python.exe / exe
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((IPC_HOST, IPC_PORT))
        sock.listen(5)
        sock.settimeout(0.4)
        _IPC_SOCK = sock
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        _signal_primary_via_socket()
        return False

    # 2) Named mutex as extra guard
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            ctypes.set_last_error(0)
            _MUTEX = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                _signal_primary_via_socket()
                try:
                    sock.close()
                except OSError:
                    pass
                _IPC_SOCK = None
                return False
        except (OSError, AttributeError):
            pass

    return True


def start_activate_watcher(on_activate: Callable[[], None]) -> None:
    """When a second launch connects, show the primary window."""
    global _WATCH_STOP
    if _IPC_SOCK is None:
        return
    _WATCH_STOP = threading.Event()
    sock = _IPC_SOCK

    def _loop() -> None:
        while _WATCH_STOP is not None and not _WATCH_STOP.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64)
            except OSError:
                data = b""
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            if data.startswith(b"SHOW"):
                try:
                    on_activate()
                except Exception:
                    pass

    threading.Thread(target=_loop, name="largo-activate", daemon=True).start()


def stop_activate_watcher() -> None:
    global _IPC_SOCK
    if _WATCH_STOP is not None:
        _WATCH_STOP.set()
    if _IPC_SOCK is not None:
        try:
            _IPC_SOCK.close()
        except OSError:
            pass
        _IPC_SOCK = None
