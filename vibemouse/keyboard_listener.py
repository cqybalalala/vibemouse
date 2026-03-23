from __future__ import annotations

import importlib
import select
import threading
import time
from collections.abc import Callable
from typing import Protocol, cast


HotkeyCallback = Callable[[], None]


class KeyboardHotkeyListener:
    def __init__(
        self,
        *,
        on_hotkey: HotkeyCallback,
        keycodes: tuple[int, ...],
        debounce_s: float = 0.15,
        rescan_interval_s: float = 2.0,
    ) -> None:
        if not keycodes:
            raise ValueError("keycodes must not be empty")
        self._on_hotkey: HotkeyCallback = on_hotkey
        self._combo: frozenset[int] = frozenset(keycodes)
        self._debounce_s: float = max(0.0, debounce_s)
        self._rescan_interval_s: float = max(0.2, rescan_interval_s)
        self._state_lock: threading.Lock = threading.Lock()
        self._pressed: set[int] = set()
        self._combo_latched: bool = False
        self._last_fire_monotonic: float = 0.0
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        last_error_summary: str | None = None
        while not self._stop.is_set():
            evdev_error: Exception | None = None
            try:
                self._run_evdev()
                self._reset_pressed_state()
                continue
            except Exception as error:
                evdev_error = error

            try:
                self._run_pynput(timeout_s=self._rescan_interval_s)
                self._reset_pressed_state()
                continue
            except Exception as pynput_error:
                summary = (
                    f"Keyboard hotkey listener unavailable "
                    f"(evdev: {evdev_error}; pynput: {pynput_error}). Retrying..."
                )
                if summary != last_error_summary:
                    print(summary)
                    last_error_summary = summary
                self._reset_pressed_state()
                if self._stop.wait(1.0):
                    return

    def _run_pynput(self, *, timeout_s: float | None = None) -> None:
        try:
            keyboard_module = importlib.import_module("pynput.keyboard")
        except Exception as error:
            raise RuntimeError("pynput.keyboard is not available") from error

        listener_ctor = cast(_KeyboardListenerCtor, getattr(keyboard_module, "Listener"))
        key_class = getattr(keyboard_module, "Key")

        def _keycode_for(key: object) -> int | None:
            try:
                vk = getattr(key, "vk", None)
                if vk is not None:
                    return int(vk)
                value = getattr(key, "value", None)
                if value is not None:
                    vk2 = getattr(value, "vk", None)
                    if vk2 is not None:
                        return int(vk2)
            except Exception:
                pass
            return None

        combo_vks: frozenset[int] = frozenset()

        def on_press(key: object) -> None:
            nonlocal combo_vks
            vk = _keycode_for(key)
            if vk is None:
                return
            with self._state_lock:
                self._pressed.add(vk)
                if self._combo_latched and not self._combo.issubset(self._pressed):
                    self._combo_latched = False
                import time as _time
                now = _time.monotonic()
                if (
                    not self._combo_latched
                    and self._combo.issubset(self._pressed)
                    and now - self._last_fire_monotonic >= self._debounce_s
                ):
                    self._combo_latched = True
                    self._last_fire_monotonic = now
                    should_fire = True
                else:
                    should_fire = False
            if should_fire:
                self._on_hotkey()

        def on_release(key: object) -> None:
            vk = _keycode_for(key)
            if vk is None:
                return
            with self._state_lock:
                self._pressed.discard(vk)
                if self._combo_latched and not self._combo.issubset(self._pressed):
                    self._combo_latched = False

        listener = listener_ctor(on_press=on_press, on_release=on_release)
        listener.start()
        import time as _time
        deadline: float | None = None
        if timeout_s is not None:
            deadline = _time.monotonic() + max(0.2, timeout_s)
        try:
            while not self._stop.is_set():
                if deadline is not None and _time.monotonic() >= deadline:
                    return
                _time.sleep(0.2)
        finally:
            listener.stop()

    def _run_evdev(self) -> None:
        try:
            evdev_module = importlib.import_module("evdev")
        except Exception as error:
            raise RuntimeError("evdev is not available") from error

        input_device_ctor = cast(_InputDeviceCtor, getattr(evdev_module, "InputDevice"))
        ecodes = cast(_Ecodes, getattr(evdev_module, "ecodes"))
        list_devices = cast(_ListDevicesFn, getattr(evdev_module, "list_devices"))

        devices: list[_EvdevDevice] = []
        for path in list_devices():
            try:
                dev = input_device_ctor(path)
            except Exception:
                continue
            try:
                caps = dev.capabilities()
                key_cap = caps.get(ecodes.EV_KEY, [])
                if not any(code in key_cap for code in self._combo):
                    dev.close()
                    continue
                if ecodes.KEY_A not in key_cap:
                    dev.close()
                    continue
                devices.append(dev)
            except Exception:
                dev.close()

        if not devices:
            raise RuntimeError("No keyboard input device with required keycodes found")

        try:
            fd_map: dict[int, _EvdevDevice] = {dev.fd: dev for dev in devices}
            next_rescan_at = time.monotonic() + self._rescan_interval_s
            while not self._stop.is_set():
                if not fd_map:
                    return
                now = time.monotonic()
                if now >= next_rescan_at:
                    return

                timeout_s = min(0.2, max(0.0, next_rescan_at - now))
                try:
                    ready, _, _ = select.select(list(fd_map.keys()), [], [], timeout_s)
                except (OSError, ValueError):
                    return
                for fd in ready:
                    dev = fd_map[fd]
                    try:
                        events = dev.read()
                    except OSError:
                        return
                    for event in events:
                        if event.type != ecodes.EV_KEY:
                            continue
                        if self._process_key_event(event.code, event.value):
                            self._on_hotkey()
        finally:
            for dev in devices:
                dev.close()

    def _reset_pressed_state(self) -> None:
        with self._state_lock:
            self._pressed.clear()
            self._combo_latched = False

    def _process_key_event(self, keycode: int, value: int) -> bool:
        with self._state_lock:
            if value == 1:
                self._pressed.add(keycode)
            elif value == 0:
                self._pressed.discard(keycode)
            else:
                return False

            if self._combo_latched and not self._combo.issubset(self._pressed):
                self._combo_latched = False

            now = time.monotonic()
            if (
                not self._combo_latched
                and self._combo.issubset(self._pressed)
                and now - self._last_fire_monotonic >= self._debounce_s
            ):
                self._combo_latched = True
                self._last_fire_monotonic = now
                return True
            return False


class _EvdevEvent(Protocol):
    type: int
    value: int
    code: int


class _EvdevDevice(Protocol):
    fd: int

    def read(self) -> list[_EvdevEvent]: ...

    def capabilities(self) -> dict[int, list[int]]: ...

    def close(self) -> None: ...


class _InputDeviceCtor(Protocol):
    def __call__(self, path: str) -> _EvdevDevice: ...


class _ListDevicesFn(Protocol):
    def __call__(self) -> list[str]: ...


class _Ecodes(Protocol):
    EV_KEY: int
    KEY_A: int


class _KeyboardListener(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class _KeyboardListenerCtor(Protocol):
    def __call__(
        self,
        *,
        on_press: Callable[[object], None] | None = None,
        on_release: Callable[[object], None] | None = None,
    ) -> _KeyboardListener: ...
