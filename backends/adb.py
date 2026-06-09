"""ADB 设备后端（Android 无线 + USB）"""

import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Optional

from .base import DeviceBackend, DeviceInfo

log = logging.getLogger("wechat_batch")

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_ADB = os.path.join(PROJECT_DIR, "tools", "platform-tools", "adb")
ADB_BIN = os.environ.get("ADB_BIN") or (BUNDLED_ADB if os.path.exists(BUNDLED_ADB) else "adb")


class AdbBackend(DeviceBackend):
    """Android ADB 后端，支持 USB 和无线两种连接模式"""

    def __init__(self, mode: str = "auto"):
        """
        mode: "usb" | "wireless" | "auto"
          - usb: 仅使用 USB 连接的设备，不尝试 adb connect
          - wireless: 使用无线 ADB，可执行 adb connect
          - auto: 优先 USB，找不到再尝试无线
        """
        self.mode = mode
        self._serial: Optional[str] = None  # 当前设备的序列号（USB）或 IP:port（无线）

    # ── 底层 ADB 命令 ──────────────────────────────────────

    @staticmethod
    def _raw_adb(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
        """执行原始 adb 命令（不指定设备）"""
        try:
            r = subprocess.run(
                [ADB_BIN] + cmd, capture_output=True, text=True, timeout=timeout
            )
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return -1, "", ""

    def _get_device_arg(self) -> list[str]:
        """获取 -s serial 参数（如果已选定设备）"""
        if self._serial:
            return ["-s", self._serial]
        return []

    def _adb(self, cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
        """执行带设备参数的 adb 命令"""
        return self._raw_adb(self._get_device_arg() + cmd, timeout)

    def _adb_output(self, cmd: list[str], timeout: int = 15) -> str:
        """执行 adb 命令并只返回 stdout"""
        _, out, _ = self._adb(cmd, timeout)
        return out

    # ── 设备发现 ──────────────────────────────────────────

    def _list_devices(self) -> list[tuple[str, str]]:
        """
        列出所有 adb 设备，返回 [(serial, type), ...]
        type: "usb" | "wireless" | "unknown"
        """
        _, out, _ = self._raw_adb(["devices"])
        devices = []
        for line in out.split("\n")[1:]:
            line = line.strip()
            if not line or "\tdevice" not in line:
                continue
            serial = line.split("\t")[0]
            if "._adb-tls-connect" in serial:
                continue  # 跳过残留的 TLS 连接
            if ":" in serial:
                devices.append((serial, "wireless"))
            else:
                devices.append((serial, "usb"))
        return devices

    def _find_usb_device(self) -> Optional[str]:
        """查找 USB 连接（非无线）的设备，返回 serial 或 None"""
        for serial, dtype in self._list_devices():
            if dtype == "usb":
                return serial
        return None

    def _find_wireless_device(self) -> Optional[str]:
        """查找已通过无线连接的设备"""
        for serial, dtype in self._list_devices():
            if dtype == "wireless":
                return serial
        return None

    def _try_connect_wireless(self, ports: list[int] = None) -> Optional[str]:
        """尝试连接 localhost 上的常见无线调试端口"""
        if ports is None:
            ports = [7555, 22471, 5555]
        for port in ports:
            conn = f"127.0.0.1:{port}"
            rc, out, _ = self._raw_adb(["connect", conn], timeout=5)
            time.sleep(0.5)
            if rc == 0 and "connected" in out.lower():
                # 断开可能残留的 TLS 连接
                for serial, _ in self._list_devices():
                    if "._adb-tls-connect" in serial:
                        self._raw_adb(["disconnect", serial])
                return conn
        return None

    # ── 公开接口实现 ──────────────────────────────────────

    def connect(self, **kwargs) -> bool:
        """
        连接设备。
        无线模式：需要 conn="IP:port" 参数（也可不给，自动尝试常见端口）
        USB 模式：自动检测 USB 设备
        auto 模式：优先 USB，再尝试无线
        返回是否成功。
        """
        # 先断开旧设备
        self.disconnect()

        if self.mode == "usb":
            serial = self._find_usb_device()
            if serial:
                self._serial = serial
                log.info(f"✅ USB 设备已连接: {self.get_device_name()} ({serial})")
                return True
            log.error("❌ 未检测到 USB 设备，请确认：")
            log.error("   1. USB 线已连接")
            log.error("   2. 手机已开启 USB 调试")
            log.error("   3. 手机上已授权此电脑的调试请求")
            return False

        elif self.mode == "wireless":
            conn = kwargs.get("conn", "")
            if conn:
                rc, out, _ = self._raw_adb(["connect", conn], timeout=15)
                if rc == 0 and "connected" in out.lower():
                    # 清除残留 TLS
                    for s, _ in self._list_devices():
                        if "._adb-tls-connect" in s:
                            self._raw_adb(["disconnect", s])
                    self._serial = conn
                    log.info(f"✅ 无线已连接: {self.get_device_name()} ({conn})")
                    return True
                log.error(f"❌ 连接失败: {out}")
                return False
            else:
                # 不给地址，先检查是否已有无线设备
                serial = self._find_wireless_device()
                if serial:
                    self._serial = serial
                    return True
                # 尝试常见端口
                serial = self._try_connect_wireless()
                if serial:
                    self._serial = serial
                    log.info(f"✅ 自动连接: {self.get_device_name()} ({serial})")
                    return True
                log.error("❌ 未检测到无线设备，请提供 IP:端口")
                return False

        else:  # auto
            # 优先 USB
            serial = self._find_usb_device()
            if serial:
                self._serial = serial
                log.info(f"✅ USB 设备: {self.get_device_name()} ({serial})")
                return True
            # 再尝试无线
            serial = self._find_wireless_device()
            if serial:
                self._serial = serial
                log.info(f"✅ 无线设备: {self.get_device_name()} ({serial})")
                return True
            serial = self._try_connect_wireless()
            if serial:
                self._serial = serial
                log.info(f"✅ 自动连接: {self.get_device_name()} ({serial})")
                return True
            log.error("❌ 未检测到任何设备")
            return False

    def disconnect(self):
        """断开当前设备"""
        if self._serial and ":" in self._serial:
            # 无线连接才需要 disconnect
            self._raw_adb(["disconnect", self._serial])
        self._serial = None

    def is_connected(self) -> bool:
        """检查当前设备是否在线"""
        if not self._serial:
            # 还没选设备，尝试 auto-detect
            serial = self._find_usb_device() or self._find_wireless_device()
            if serial:
                self._serial = serial
                return True
            return False
        # 验证当前设备还在线
        devices = self._list_devices()
        for serial, _ in devices:
            if serial == self._serial:
                return True
        return False

    def get_device_name(self) -> str:
        """获取设备型号名称"""
        name = self._adb_output(["shell", "getprop", "ro.product.model"])
        return name or "未知设备"

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率 (宽, 高)"""
        out = self._adb_output(["shell", "wm", "size"])
        m = re.search(r"(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (1080, 2340)

    def tap(self, x: int, y: int):
        """点击屏幕坐标"""
        rc, _, err = self._adb(["shell", "input", "tap", str(x), str(y)])
        if rc != 0:
            log.warning(f"点击失败 ({x},{y}): {err}")
            time.sleep(0.3)
            self._adb(["shell", "input", "tap", str(x), str(y)])

    def back(self):
        """按返回键"""
        self._adb(["shell", "input", "keyevent", "KEYCODE_BACK"])

    def push_file(self, local_path: str, remote_path: str):
        """推送文件到设备"""
        self._adb(["push", str(local_path), remote_path], timeout=10)

    def set_file_mtime(self, remote_path: str, timestamp: float = None) -> bool:
        """设置设备端文件修改时间，用于修正相册排序/显示时间。"""
        if timestamp is None:
            cmd = ["shell", "touch", remote_path]
        else:
            stamp = time.strftime("%Y%m%d%H%M.%S", time.localtime(timestamp))
            cmd = ["shell", "touch", "-t", stamp, remote_path]
        rc, _, err = self._adb(cmd, timeout=5)
        if rc != 0:
            log.debug(f"设置文件时间失败: {remote_path}: {err}")
            return False
        return True

    def screenshot(self, local_path: str):
        """截屏并保存到本地"""
        remote = "/sdcard/wechat_batch_screenshot.png"
        rc, _, err = self._adb(["shell", "screencap", "-p", remote], timeout=10)
        if rc != 0:
            log.error(f"❌ 截屏失败 (screencap): {err}")
            return
        time.sleep(0.3)
        rc, _, err = self._adb(["pull", remote, local_path], timeout=10)
        if rc != 0:
            log.error(f"❌ 截屏失败 (pull): {err}")

    def dump_ui(self) -> str:
        """导出当前界面的 UI XML，用于判断是否仍停留在某个按钮页面"""
        remote = "/sdcard/wechat_batch_ui.xml"
        rc, _, err = self._adb(["shell", "uiautomator", "dump", remote], timeout=8)
        if rc != 0:
            log.debug(f"UI dump 失败: {err}")
            return ""
        _, out, err = self._adb(["shell", "cat", remote], timeout=8)
        if err:
            log.debug(f"读取 UI dump 警告: {err}")
        return out or ""

    def check_wechat_installed(self) -> bool:
        """检查微信是否安装"""
        out = self._adb_output(["shell", "pm", "list", "packages", "com.tencent.mm"])
        return "com.tencent.mm" in out

    def media_scan(self, file_path: str):
        """触发媒体扫描，让推送到设备的图片在图库中可见"""
        self._adb(
            ["shell", "am", "broadcast",
             "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
             "-d", f"file://{file_path}"],
            timeout=5,
        )

    def get_device_info(self) -> DeviceInfo:
        """获取完整设备信息"""
        w, h = self.get_screen_size()
        return DeviceInfo(
            name=self.get_device_name(),
            screen_w=w,
            screen_h=h,
            backend_type="adb",
        )

    def get_serial(self) -> Optional[str]:
        """获取当前设备序列号 / 连接字符串"""
        return self._serial
