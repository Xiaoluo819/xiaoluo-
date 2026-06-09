"""设备后端抽象基类"""

from abc import ABC, abstractmethod
from typing import Optional, NamedTuple


class DeviceInfo(NamedTuple):
    name: str
    screen_w: int
    screen_h: int
    backend_type: str  # "adb" | "ios"


class DeviceBackend(ABC):
    """所有设备后端的抽象接口"""

    @abstractmethod
    def connect(self, **kwargs) -> bool:
        """建立连接，返回是否成功"""
        ...

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """检查是否已连接"""
        ...

    @abstractmethod
    def get_device_name(self) -> str:
        """获取设备名称/型号"""
        ...

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率 (宽, 高)"""
        ...

    @abstractmethod
    def tap(self, x: int, y: int):
        """点击指定坐标"""
        ...

    @abstractmethod
    def back(self):
        """按返回键 / 返回上一页"""
        ...

    @abstractmethod
    def push_file(self, local_path: str, remote_path: str):
        """推送文件到设备"""
        ...

    def set_file_mtime(self, remote_path: str, timestamp: float = None) -> bool:
        """设置设备端文件修改时间；timestamp 为 None 时使用设备当前时间。"""
        return False

    @abstractmethod
    def screenshot(self, local_path: str):
        """截取设备屏幕并保存到 local_path"""
        ...

    def dump_ui(self) -> str:
        """导出当前界面的 UI 文本/XML；不支持时返回空字符串"""
        return ""

    @abstractmethod
    def check_wechat_installed(self) -> bool:
        """检查微信是否已安装"""
        ...

    @abstractmethod
    def media_scan(self, file_path: str):
        """触发媒体扫描（让推送的图片在图库中可见）"""
        ...

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        """获取完整设备信息"""
        ...
