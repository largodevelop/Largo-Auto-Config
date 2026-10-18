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


def enable_glass(root, tint: int = 0x0F0F0F, opacity: int = 0x99, blur: bool = True) -> bool:
    """Frosted dark glass: blur of whatever is behind the window (Windows 10/11 acrylic)."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = _hwnd_from_tk(root)

        class AccentPolicy(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WinCompAttr(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]

        r, g, b = (tint >> 16) & 0xFF, (tint >> 8) & 0xFF, tint & 0xFF
        policy = AccentPolicy(4 if blur else 2, 2 if blur else 0, (opacity << 24) | (b << 16) | (g << 8) | r, 0)  # 4 = acrylic blur, 2 = plain tint (used while dragging: blur makes the move laggy)
        data = WinCompAttr(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p), ctypes.sizeof(policy))
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except (OSError, AttributeError):
        return False


def window_origin(root) -> tuple[int, int]:
    """Real top-left corner of the window on screen (Tk's own idea of it can go stale after the window was moved)."""
    try:
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(_hwnd_from_tk(root), ctypes.byref(rect)):
            return int(rect.left), int(rect.top)
    except (OSError, AttributeError):
        pass
    return int(root.winfo_rootx()), int(root.winfo_rooty())


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


# ---- Прогресс на кнопке в панели задач (ITaskbarList3) -----------------------------------------------------------

_TBPF_NOPROGRESS = 0
_TBPF_INDETERMINATE = 1
_TBPF_NORMAL = 2
_TBPF_ERROR = 4


def _taskbar_list():
    """COM-объект ITaskbarList3 для текущего потока (None, если Windows его не дала)."""
    import threading

    cache = getattr(_taskbar_list, "cache", None)
    if cache is None:
        cache = _taskbar_list.cache = threading.local()
    if getattr(cache, "ready", False):
        return cache.obj
    cache.ready = True
    cache.obj = None
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        clsid, iid = GUID(), GUID()
        ole32.CLSIDFromString("{56FDF344-FD6D-11d0-958A-006097C9A090}", ctypes.byref(clsid))
        ole32.CLSIDFromString("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}", ctypes.byref(iid))
        pointer = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)) != 0 or not pointer.value:
            return None
        vtable = ctypes.cast(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        proto = ctypes.WINFUNCTYPE
        hr_init = proto(ctypes.c_long, ctypes.c_void_p)(vtable[3])
        set_value = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)(vtable[9])
        set_state = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vtable[10])
        hr_init(pointer)
        cache.obj = (pointer, set_value, set_state)
    except Exception:
        cache.obj = None
    return cache.obj


def window_handle(root) -> int:
    return _hwnd_from_tk(root)


def set_taskbar_progress(hwnd, done=None, total=None):
    """Полоса загрузки на кнопке окна в панели задач. done=None — убрать, total=0 — «идёт, сколько неизвестно»."""
    if sys.platform != "win32" or not hwnd:
        return False
    obj = _taskbar_list()
    if obj is None:
        return False
    pointer, set_value, set_state = obj
    try:
        if done is None:
            set_state(pointer, hwnd, _TBPF_NOPROGRESS)
        elif not total:
            set_state(pointer, hwnd, _TBPF_INDETERMINATE)
        else:
            set_state(pointer, hwnd, _TBPF_NORMAL)
            set_value(pointer, hwnd, int(done), int(total))
        return True
    except Exception:
        return False
