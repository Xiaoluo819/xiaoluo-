"""设备后端工厂 — 自动检测或手动指定"""

from .base import DeviceBackend, DeviceInfo
from .adb import AdbBackend


def create_backend(device_type: str = "auto", **kwargs) -> DeviceBackend:
    """
    创建设备后端。

    device_type:
      - "android" / "adb"  → AdbBackend（保持现有行为）
      - "ios"              → 暂未实现，抛出 NotImplementedError
      - "auto"             → 自动检测（目前只支持 Android）

    kwargs 传给后端构造函数，例如 AdbBackend(mode="usb")
    """
    device_type = device_type.lower()

    if device_type in ("android", "adb", "auto"):
        return AdbBackend(**kwargs)

    elif device_type == "ios":
        raise NotImplementedError(
            "iOS 后端尚未实现。需要 pymobiledevice3 + WDA，计划后续版本支持。"
        )

    else:
        raise ValueError(f"不支持的设备类型: {device_type}，可选: android, ios, auto")
