"""Audio output routing shared by every in-app TTS player.

Qt creates a ``QAudioOutput`` with whichever device was default at creation
time.  That is not sufficient on macOS: users commonly connect AirPods after
opening Voice Studio, or want to audition on a non-default output.  This
widget keeps the player on the current system route by default and exposes an
explicit device choice when needed.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


def _device_id(device: QAudioDevice) -> bytes:
    """Return a stable comparable id for a Qt audio device."""

    return bytes(device.id().data())


class AudioOutputSelector(QWidget):
    """Bind a ``QAudioOutput`` to system-following or user-selected routing."""

    route_changed = Signal(str)

    def __init__(self, audio_output: QAudioOutput, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._audio_output = audio_output
        self._media_devices = QMediaDevices(self)
        self._shutdown_done = False
        self._following_system_default = True
        self._manual_device_id = b""
        self._devices: dict[bytes, QAudioDevice] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel("输出设备")
        label.setObjectName("audioOutputLabel")
        layout.addWidget(label)
        self._device_combo = QComboBox()
        self._device_combo.setObjectName("audioOutputDeviceCombo")
        self._device_combo.setAccessibleName("音频输出设备")
        self._device_combo.setToolTip("默认跟随 macOS 系统输出；也可指定耳机或扬声器。")
        self._device_combo.setMinimumContentsLength(14)
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        layout.addWidget(self._device_combo)

        self._media_devices.audioOutputsChanged.connect(self.refresh)
        self.refresh()

    @property
    def is_following_system_default(self) -> bool:
        """Whether this player follows macOS/the OS's current default route."""

        return self._following_system_default

    def refresh(self) -> None:
        """Refresh available devices and apply a changed system default route."""

        if self._shutdown_done:
            return

        default_device = QMediaDevices.defaultAudioOutput()
        devices = list(QMediaDevices.audioOutputs())
        self._devices = {_device_id(device): device for device in devices}

        default_name = default_device.description() or "系统默认设备"
        previous_id = self._manual_device_id
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem(f"跟随系统输出（当前：{default_name}）", None)
        for device in devices:
            self._device_combo.addItem(device.description() or "未命名输出设备", _device_id(device))

        if self._following_system_default:
            self._device_combo.setCurrentIndex(0)
        elif previous_id in self._devices:
            self._device_combo.setCurrentIndex(self._device_combo.findData(previous_id))
        else:
            # A disconnected manual headset must never leave the player bound
            # to a stale route; recover to the system default deterministically.
            self._following_system_default = True
            self._manual_device_id = b""
            self._device_combo.setCurrentIndex(0)
        self._device_combo.blockSignals(False)

        if self._following_system_default:
            self._audio_output.setDevice(default_device)
            self.route_changed.emit(default_name)

    def _on_device_selected(self, index: int) -> None:
        if index <= 0:
            self._following_system_default = True
            self._manual_device_id = b""
            device = QMediaDevices.defaultAudioOutput()
            self._audio_output.setDevice(device)
            self.route_changed.emit(device.description() or "系统默认设备")
            return

        selected_id = self._device_combo.itemData(index)
        if not isinstance(selected_id, bytes):
            return
        selected_device = self._devices.get(selected_id)
        if selected_device is None:
            self.refresh()
            return
        self._following_system_default = False
        self._manual_device_id = selected_id
        self._audio_output.setDevice(selected_device)
        self.route_changed.emit(selected_device.description() or "未命名输出设备")

    def shutdown(self) -> None:
        """Disconnect native device notifications before the owning player is destroyed."""

        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self._media_devices.audioOutputsChanged.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        self._devices.clear()
