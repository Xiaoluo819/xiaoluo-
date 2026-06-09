#!/bin/bash
cd "$(dirname "$0")"

if [ -x "./tools/platform-tools/adb" ]; then
  export PATH="$PWD/tools/platform-tools:$PATH"
fi

check_deps() {
  python3 -c "import yaml, cv2" 2>/dev/null && return 0
  echo "========================================"
  echo "  首次运行，安装依赖..."
  echo "========================================"
  pip3 install --user opencv-python pyyaml
  echo ""
}

check_adb() {
  which adb >/dev/null 2>&1 && return 0
  echo "========================================"
  echo "  未检测到 ADB 工具"
  echo "========================================"
  echo "  ADB 用来连接和控制 Android 手机。"
  echo ""
  if which brew >/dev/null 2>&1; then
    echo "  正在尝试通过 Homebrew 安装："
    echo "  brew install android-platform-tools"
    echo ""
    brew install android-platform-tools
  else
    echo "  没有检测到 Homebrew，无法自动运行："
    echo "  brew install android-platform-tools"
    echo ""
    echo "  你可以任选一种方式处理："
    echo "  1. 先安装 Homebrew，再重新打开本软件"
    echo "  2. 手动安装 Android SDK Platform Tools"
    echo ""
    echo "  手动安装方式："
    echo "  1. 打开官方页面："
    echo "     https://developer.android.com/tools/releases/platform-tools"
    echo "  2. 下载 macOS 版本并解压"
    echo "  3. 找到解压后的 platform-tools 文件夹"
    echo "  4. 在终端运行：cd 解压后的/platform-tools"
    echo "  5. 再运行：./adb version"
    echo "  6. 能看到版本号，就说明 ADB 可用"
    echo ""
    echo "  如果终端执行 adb version 有输出，就说明 ADB 已经可用。"
    read -p "  安装好 ADB 后按 Enter 继续..."
  fi
  echo ""
}

check_adb
check_deps

while true; do
  clear
  DEVINFO=$(python3 -c "
from backends import create_backend
b = create_backend()
if b.is_connected():
    info = b.get_device_info()
    sn = b.get_serial() or '?'
    conn_type = 'USB' if sn and ':' not in str(sn) else '无线'
    print(f'🟢 {info.name}  ({conn_type})')
else:
    print('🔴 未连接')
" 2>/dev/null)

  echo "========================================"
  echo "       微信批量扫码加群  v2.5 Codex"
  echo "========================================"
  echo "   $DEVINFO"
  echo "========================================"
  echo ""
  echo "  1. 连接设备（无线 / USB）"
  echo "  2. 检查环境"
  echo "  3. 校准按钮 + 导出坐标"
  echo "  4. 批量扫码加群"
  echo "  5. 预览模式（只解码）"
  echo "  6. 失败重试"
  echo "  0. 退出"
  echo ""
  read -p "  请选择 (0-6): " choice

  case $choice in
    1)
      clear
      echo "========================================"
      echo "       第一步：连接手机"
      echo "========================================"
      echo ""
      echo "  第一次使用建议选择 USB 连接，最稳定，也最容易排查。"
      echo ""
      echo "  方式 A：USB 连接（推荐，华为/荣耀也建议用这个）"
      echo "    1. 用支持数据传输的 USB 线连接手机和电脑"
      echo "    2. 手机打开：设置 → 关于手机 → 连续点击「版本号」7 次"
      echo "    3. 手机打开：设置 → 开发者选项 → 开启「USB 调试」"
      echo "    4. 华为/荣耀建议同时开启「仅充电模式下允许 ADB 调试」"
      echo "    5. 手机弹出「允许 USB 调试」时，勾选「一律允许」并点确定"
      echo "    6. 然后在下面选择 2"
      echo ""
      echo "  方式 B：无线连接（Android 11+，需要无线调试）"
      echo "    1. 手机和电脑连接同一个 Wi-Fi"
      echo "    2. 手机打开：设置 → 开发者选项 → 无线调试"
      echo "    3. 已配对过：直接输入「IP 地址和端口」"
      echo "    4. 第一次无线连接：先选「使用配对码配对设备」"
      echo "    5. 按提示输入「配对 IP:端口」和「配对码」"
      echo "    6. 配对成功后，返回无线调试主页，再输入「IP 地址和端口」连接"
      echo ""
      echo "  连接成功后，菜单顶部会显示 🟢 手机型号。"
      echo "----------------------------------------"
      echo "  1. Android 无线连接"
      echo "  2. Android USB 连接（推荐）"
      echo "  0. 返回"
      echo ""
      read -p "  请选择 (0-2): " conn_choice
      case $conn_choice in
        1) python3 wechat_batch.py pair --mode wireless ;;
        2) python3 wechat_batch.py pair --mode usb ;;
        0) ;;
        *) echo "无效选择"; sleep 1 ;;
      esac
      ;;
    2) python3 wechat_batch.py setup ;;
    3) python3 wechat_batch.py calibrate ;;
    4) python3 wechat_batch.py scan ;;
    5) python3 wechat_batch.py scan --dry-run ;;
    6) python3 wechat_batch.py retry ;;
    0) echo "再见"; exit 0 ;;
    *) echo "无效选择"; sleep 1; continue ;;
  esac

  echo ""
  read -p "按 Enter 返回菜单..."
done
