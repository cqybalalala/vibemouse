from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Protocol, cast


_TERMINAL_CLASS_HINTS: set[str] = {
    "foot",
    "kitty",
    "alacritty",
    "wezterm",
    "ghostty",
    "gnome-terminal",
    "gnome-terminal-server",
    "konsole",
    "tilix",
    "xterm",
    "terminator",
    "xfce4-terminal",
    "urxvt",
    "st",
    "tabby",
    "hyper",
    "warp",
    "windowsterminal",
    "wt",
}

_TERMINAL_TITLE_HINTS: set[str] = {
    "terminal",
    "tmux",
    "bash",
    "zsh",
    "fish",
    "powershell",
    "cmd.exe",
}


def is_terminal_window_payload(payload: Mapping[str, object]) -> bool:
    window_class = str(payload.get("class", "")).lower()
    initial_class = str(payload.get("initialClass", "")).lower()
    title = str(payload.get("title", "")).lower()

    if any(
        hint in window_class or hint in initial_class for hint in _TERMINAL_CLASS_HINTS
    ):
        return True

    return any(hint in title for hint in _TERMINAL_TITLE_HINTS)


class SystemIntegration(Protocol):
    @property
    def is_hyprland(self) -> bool: ...

    def send_shortcut(self, *, mod: str, key: str) -> bool: ...

    def active_window(self) -> dict[str, object] | None: ...

    def cursor_position(self) -> tuple[int, int] | None: ...

    def move_cursor(self, *, x: int, y: int) -> bool: ...

    def switch_workspace(self, direction: str) -> bool: ...

    def is_text_input_focused(self) -> bool | None: ...

    def send_enter_via_accessibility(self) -> bool | None: ...

    def is_terminal_window_active(self) -> bool | None: ...

    def paste_shortcuts(
        self, *, terminal_active: bool
    ) -> tuple[tuple[str, str], ...]: ...


class NoopSystemIntegration:
    @property
    def is_hyprland(self) -> bool:
        return False

    def send_shortcut(self, *, mod: str, key: str) -> bool:
        del mod
        del key
        return False

    def active_window(self) -> dict[str, object] | None:
        return None

    def cursor_position(self) -> tuple[int, int] | None:
        return None

    def move_cursor(self, *, x: int, y: int) -> bool:
        del x
        del y
        return False

    def switch_workspace(self, direction: str) -> bool:
        del direction
        return False

    def is_text_input_focused(self) -> bool | None:
        return None

    def send_enter_via_accessibility(self) -> bool | None:
        return None

    def is_terminal_window_active(self) -> bool | None:
        return None

    def paste_shortcuts(self, *, terminal_active: bool) -> tuple[tuple[str, str], ...]:
        del terminal_active
        return ()


class HyprlandSystemIntegration:
    @property
    def is_hyprland(self) -> bool:
        return True

    def send_shortcut(self, *, mod: str, key: str) -> bool:
        mod_part = mod.strip().upper()
        if mod_part:
            arg = f"{mod_part}, {key}, activewindow"
        else:
            arg = f", {key}, activewindow"
        return self._dispatch(["sendshortcut", arg], timeout=1.0)

    def active_window(self) -> dict[str, object] | None:
        return self._query_json(["activewindow"], timeout=1.0)

    def cursor_position(self) -> tuple[int, int] | None:
        payload = self._query_json(["cursorpos"], timeout=0.8)
        if payload is None:
            return None

        x_raw = payload.get("x")
        y_raw = payload.get("y")
        if not isinstance(x_raw, int | float) or not isinstance(y_raw, int | float):
            return None

        return int(x_raw), int(y_raw)

    def move_cursor(self, *, x: int, y: int) -> bool:
        return self._dispatch(["movecursor", str(x), str(y)], timeout=0.8)

    def switch_workspace(self, direction: str) -> bool:
        workspace_arg = "e-1" if direction == "left" else "e+1"
        return self._dispatch(["workspace", workspace_arg], timeout=1.0)

    def is_text_input_focused(self) -> bool | None:
        return probe_text_input_focus_via_atspi()

    def send_enter_via_accessibility(self) -> bool | None:
        return probe_send_enter_via_atspi()

    def is_terminal_window_active(self) -> bool | None:
        payload = self.active_window()
        if payload is None:
            return False
        return is_terminal_window_payload(payload)

    def paste_shortcuts(self, *, terminal_active: bool) -> tuple[tuple[str, str], ...]:
        if terminal_active:
            return (
                ("CTRL SHIFT", "V"),
                ("SHIFT", "Insert"),
                ("CTRL", "V"),
            )
        return (("CTRL", "V"),)

    @staticmethod
    def _dispatch(args: list[str], *, timeout: float) -> bool:
        try:
            proc = subprocess.run(
                ["hyprctl", "dispatch", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        return proc.returncode == 0 and proc.stdout.strip() == "ok"

    @staticmethod
    def _query_json(args: list[str], *, timeout: float) -> dict[str, object] | None:
        try:
            proc = subprocess.run(
                ["hyprctl", "-j", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if proc.returncode != 0:
            return None

        try:
            payload_obj = cast(object, json.loads(proc.stdout))
        except json.JSONDecodeError:
            return None

        if not isinstance(payload_obj, dict):
            return None

        return cast(dict[str, object], payload_obj)


class WindowsSystemIntegration:
    """Windows platform integration using Win32 API via ctypes."""

    _TEXT_CONTROL_CLASSES: frozenset[str] = frozenset({
        "edit",
        "richedit20w",
        "richedit50w",
        "richtext",
        "scintilla",
        "chrome_renderwidgethosthwnd",
        "mozillawindowclass",
        "mozillacontentwindowclass",
    })

    _TERMINAL_CLASSES: frozenset[str] = frozenset({
        "consolepswindow",
        "consolewindowclass",
        "windowsterminal",
        "mintty",
        "conemu",
    })

    @property
    def is_hyprland(self) -> bool:
        return False

    def send_shortcut(self, *, mod: str, key: str) -> bool:
        return False

    def active_window(self) -> dict[str, object] | None:
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            title_buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buf, 512)
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            return {"title": title_buf.value, "class": class_buf.value}
        except Exception:
            return None

    def cursor_position(self) -> tuple[int, int] | None:
        try:
            import ctypes
            import ctypes.wintypes
            point = ctypes.wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
                return point.x, point.y
        except Exception:
            pass
        return None

    def move_cursor(self, *, x: int, y: int) -> bool:
        try:
            import ctypes
            return bool(ctypes.windll.user32.SetCursorPos(x, y))  # type: ignore[attr-defined]
        except Exception:
            return False

    def switch_workspace(self, direction: str) -> bool:
        return False

    def is_text_input_focused(self) -> bool | None:
        try:
            import ctypes
            import ctypes.wintypes

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.DWORD),
                    ("flags", ctypes.wintypes.DWORD),
                    ("hwndActive", ctypes.wintypes.HWND),
                    ("hwndFocus", ctypes.wintypes.HWND),
                    ("hwndCapture", ctypes.wintypes.HWND),
                    ("hwndMenuOwner", ctypes.wintypes.HWND),
                    ("hwndMoveSize", ctypes.wintypes.HWND),
                    ("hwndCaret", ctypes.wintypes.HWND),
                    ("rcCaret", ctypes.wintypes.RECT),
                ]

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            fg_hwnd = user32.GetForegroundWindow()
            if not fg_hwnd:
                return True

            thread_id = user32.GetWindowThreadProcessId(fg_hwnd, None)
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                return True

            focused_hwnd = info.hwndFocus
            if not focused_hwnd:
                return True

            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(focused_hwnd, class_buf, 256)
            return class_buf.value.lower() in self._TEXT_CONTROL_CLASSES
        except Exception:
            return True

    def send_enter_via_accessibility(self) -> bool | None:
        return None

    def is_terminal_window_active(self) -> bool | None:
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            fg_hwnd = user32.GetForegroundWindow()
            if not fg_hwnd:
                return False
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(fg_hwnd, class_buf, 256)
            class_lower = class_buf.value.lower()
            title_buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(fg_hwnd, title_buf, 512)
            title_lower = title_buf.value.lower()
            if any(hint in class_lower for hint in self._TERMINAL_CLASSES):
                return True
            return any(hint in title_lower for hint in _TERMINAL_TITLE_HINTS)
        except Exception:
            return False

    def paste_shortcuts(self, *, terminal_active: bool) -> tuple[tuple[str, str], ...]:
        if terminal_active:
            return (("CTRL SHIFT", "V"), ("CTRL", "V"))
        return (("CTRL", "V"),)


def detect_hyprland_session(*, env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    desktop = source.get("XDG_CURRENT_DESKTOP", "")
    if "hyprland" in desktop.lower():
        return True
    return bool(source.get("HYPRLAND_INSTANCE_SIGNATURE"))


def create_system_integration(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> SystemIntegration:
    if detect_hyprland_session(env=env):
        return HyprlandSystemIntegration()

    resolved_platform = platform_name if platform_name is not None else sys.platform
    if resolved_platform == "win32":
        return WindowsSystemIntegration()

    return NoopSystemIntegration()


def probe_text_input_focus_via_atspi(*, timeout_s: float = 1.5) -> bool:
    script = (
        "import gi\n"
        "gi.require_version('Atspi', '2.0')\n"
        "from gi.repository import Atspi\n"
        "obj = Atspi.get_desktop(0).get_focus()\n"
        "editable = False\n"
        "role = ''\n"
        "if obj is not None:\n"
        "    role = obj.get_role_name().lower()\n"
        "    attrs = obj.get_attributes() or []\n"
        "    for it in attrs:\n"
        "        s = str(it).lower()\n"
        "        if s == 'editable:true' or s.endswith(':editable:true'):\n"
        "            editable = True\n"
        "            break\n"
        "roles = {'text', 'entry', 'password text', 'terminal', 'paragraph', 'document text', 'document web'}\n"
        "print('1' if editable or role in roles else '0')\n"
    )

    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return proc.returncode == 0 and proc.stdout.strip() == "1"


def load_atspi_module() -> object | None:
    try:
        gi = importlib.import_module("gi")
        require_version = cast(_RequireVersionFn, getattr(gi, "require_version"))
        require_version("Atspi", "2.0")
        atspi_repo = cast(object, importlib.import_module("gi.repository"))
        return cast(object, getattr(atspi_repo, "Atspi"))
    except Exception:
        return None


def probe_send_enter_via_atspi(
    *, atspi_module: object | None = None, lazy_load: bool = True
) -> bool:
    module = atspi_module
    if module is None and lazy_load:
        module = load_atspi_module()
    if module is None:
        return False

    try:
        key_synth = cast(object, getattr(module, "KeySynthType"))
        press_release = cast(object, getattr(key_synth, "PRESSRELEASE"))
        generate_keyboard_event = cast(
            _GenerateKeyboardEventFn,
            getattr(module, "generate_keyboard_event"),
        )
        return bool(generate_keyboard_event(65293, None, press_release))
    except Exception:
        return False


class _GenerateKeyboardEventFn(Protocol):
    def __call__(
        self,
        keyval: int,
        keystring: str | None,
        synth_type: object,
    ) -> bool: ...


class _RequireVersionFn(Protocol):
    def __call__(self, namespace: str, version: str) -> None: ...
