from __future__ import annotations

import os
import threading
from collections.abc import Callable

from .resources import resource_path


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]


class BackgroundTray:
    """Native Windows notification-area icon for the background worker."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_exit: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.on_open = on_open
        self.on_exit = on_exit
        self.on_error = on_error or (lambda _message: None)
        self.enabled = os.name == "nt"
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._nid = None
        self._icon_added = False
        self._lock = threading.Lock()
        self._wndproc = None

    def start(self) -> bool:
        if not self.enabled:
            return False
        if self._thread and self._thread.is_alive():
            return self._icon_added
        self._thread = threading.Thread(target=self._run_windows, name="GitPulseTray", daemon=True)
        self._thread.start()
        self._ready.wait(4)
        return self._icon_added

    def notify(self, title: str, message: str) -> bool:
        """Show a Windows notification from the persistent tray icon."""
        if not self.enabled or not self._ready.wait(3):
            return False
        with self._lock:
            if not self._icon_added or self._nid is None:
                return False
            try:
                shell32 = ctypes.windll.shell32
                shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
                shell32.Shell_NotifyIconW.restype = wintypes.BOOL
                self._nid.uFlags = 0x00000010  # NIF_INFO
                self._nid.szInfoTitle = title[:63]
                self._nid.szInfo = message[:255]
                self._nid.dwInfoFlags = 0x00000001  # NIIF_INFO
                delivered = bool(shell32.Shell_NotifyIconW(0x00000001, ctypes.byref(self._nid)))
                self._nid.uFlags = 0
                return delivered
            except Exception as exc:
                self.on_error(f"Tray notification failed: {exc}")
                return False

    def stop(self) -> None:
        if not self.enabled:
            return
        try:
            if self._hwnd:
                ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _show_menu(self, hwnd) -> None:
        user32 = ctypes.windll.user32
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, 0x00000000, 1001, "Open GitPulse")
            user32.AppendMenuW(menu, 0x00000800, 0, None)
            user32.AppendMenuW(menu, 0x00000000, 1002, "Exit background worker")
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu,
                0x00000102,  # TPM_RETURNCMD | TPM_RIGHTBUTTON
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            if command == 1001:
                try:
                    self.on_open()
                except Exception as exc:
                    self.on_error(f"Tray could not open GitPulse: {exc}")
            elif command == 1002:
                try:
                    self.on_exit()
                except Exception as exc:
                    self.on_error(f"Tray could not stop GitPulse: {exc}")
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            user32.PostMessageW(hwnd, 0x0000, 0, 0)  # WM_NULL
        finally:
            user32.DestroyMenu(menu)

    def _run_windows(self) -> None:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        class_name = f"GitPulseTrayWindow-{os.getpid()}"
        callback_message = 0x8000 + 41  # WM_APP + 41
        icon_handle = None

        try:
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.WORD
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                ctypes.c_void_p,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = _LRESULT
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL
            user32.PostQuitMessage.argtypes = [ctypes.c_int]
            user32.PostQuitMessage.restype = None
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = _LRESULT
            user32.DestroyIcon.argtypes = [wintypes.HICON]
            user32.DestroyIcon.restype = wintypes.BOOL

            def window_proc(hwnd, message, w_param, l_param):
                if message == callback_message:
                    event = int(l_param) & 0xFFFF
                    if event == 0x0203:  # WM_LBUTTONDBLCLK
                        try:
                            self.on_open()
                        except Exception as exc:
                            self.on_error(f"Tray could not open GitPulse: {exc}")
                    elif event in {0x0205, 0x007B}:  # WM_RBUTTONUP / WM_CONTEXTMENU
                        self._show_menu(hwnd)
                    return 0
                if message == 0x0010:  # WM_CLOSE
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:  # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, message, w_param, l_param)

            self._wndproc = _WNDPROC(window_proc)
            instance = kernel32.GetModuleHandleW(None)
            window_class = _WNDCLASSW()
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = instance
            window_class.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("Windows could not register the GitPulse tray window.")

            hwnd = user32.CreateWindowExW(0, class_name, "GitPulse", 0, 0, 0, 0, 0, None, None, instance, None)
            if not hwnd:
                raise OSError("Windows could not create the GitPulse tray window.")
            self._hwnd = hwnd

            icon_path = str(resource_path("assets", "gitpulse.ico"))
            icon_handle = user32.LoadImageW(None, icon_path, 1, 0, 0, 0x00000050)
            if not icon_handle:
                raise OSError("GitPulse could not load its tray icon.")

            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = 0x00000007  # NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = callback_message
            nid.hIcon = icon_handle
            nid.szTip = "GitPulse — Background automation"
            self._nid = nid
            if not shell32.Shell_NotifyIconW(0x00000000, ctypes.byref(nid)):  # NIM_ADD
                raise OSError("Windows could not add the GitPulse tray icon.")
            self._icon_added = True
            self._ready.set()

            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.on_error(f"Tray icon unavailable: {exc}")
        finally:
            self._ready.set()
            with self._lock:
                if self._icon_added and self._nid is not None:
                    shell32.Shell_NotifyIconW(0x00000002, ctypes.byref(self._nid))  # NIM_DELETE
                self._icon_added = False
            if icon_handle:
                user32.DestroyIcon(icon_handle)
            self._hwnd = None
