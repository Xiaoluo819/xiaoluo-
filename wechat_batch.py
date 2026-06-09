#!/usr/bin/env python3
"""微信批量扫码加群 v2.5 Codex 版（Android ADB / USB / 无线）

使用方法:
  python3 wechat_batch.py pair                    # 无线配对连接
  python3 wechat_batch.py pair --mode usb         # USB 连接
  python3 wechat_batch.py setup                   # 检查环境
  python3 wechat_batch.py calibrate               # 换手机校准按钮
  python3 wechat_batch.py scan                    # 批量扫码加群
  python3 wechat_batch.py scan --dry-run          # 只预览，不操作手机
  python3 wechat_batch.py retry                   # 失败图片移回 qr_input
"""

import argparse
import csv
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import termios
import time
import tty
import random
from datetime import datetime
from pathlib import Path

# ── 第三方库 ──
MISSING = []
try:
    import yaml
except ImportError:
    MISSING.append("pyyaml")
try:
    import cv2
except ImportError:
    MISSING.append("opencv-python")
if MISSING:
    print("❌ 缺少依赖: " + ", ".join(MISSING))
    print("   pip3 install " + " ".join(MISSING))
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.resolve()
log = logging.getLogger("wechat_batch")

# ── 后端导入 ──
from backends import create_backend
from backends.adb import AdbBackend
from backends.base import DeviceBackend

# ── 配置 ──
def load_config(path=None):
    p = Path(path) if path else SCRIPT_DIR / "config.yaml"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_config(config, path=None):
    p = Path(path) if path else SCRIPT_DIR / "config.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

# ── 日志 ──
def setup_logging(log_dir):
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    lf = d / f"scan_{datetime.now().strftime('%Y-%m-%d')}.log"
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S"
    )
    fh = logging.FileHandler(lf, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    lg = logging.getLogger("wechat_batch")
    lg.handlers.clear()
    lg.setLevel(logging.DEBUG)
    lg.addHandler(fh)
    lg.addHandler(ch)
    return lg, lf

# ── 单键输入（方向键微调用）──

def _getch() -> str:
    """捕获单个按键，返回方向键/字母/Enter 等。
    返回: 'UP'|'DOWN'|'LEFT'|'RIGHT'|'S-UP'|'S-DOWN'|'S-LEFT'|'S-RIGHT'|'ENTER'|'ESC'|单字符
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'UP'
                if ch3 == 'B':
                    return 'DOWN'
                if ch3 == 'C':
                    return 'RIGHT'
                if ch3 == 'D':
                    return 'LEFT'
                # Shift + 方向键: \x1b[1;2A ~ D
                if ch3 == '1':
                    rest = sys.stdin.read(3)  # ;2A ~ ;2D
                    if len(rest) >= 3 and rest[0] == ';' and rest[1] == '2':
                        if rest[2] == 'A':
                            return 'S-UP'
                        if rest[2] == 'B':
                            return 'S-DOWN'
                        if rest[2] == 'C':
                            return 'S-RIGHT'
                        if rest[2] == 'D':
                            return 'S-LEFT'
                return 'ESC'
            return 'ESC'
        elif ch in ('\r', '\n'):
            return 'ENTER'
        elif ch == '\x03':  # Ctrl+C
            raise KeyboardInterrupt()
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _mark_screenshot(path: str, x: int, y: int, history=None, label: str = ""):
    """在截图上标出当前点击位置和历史试点"""
    if not os.path.exists(path):
        log.warning(f"⚠️ 截图文件不存在: {path}，请检查 ADB 连接")
        return
    img = cv2.imread(path)
    if img is None:
        log.warning(f"⚠️ 无法读取截图: {path}")
        return
    history = history or []
    for idx, (hx, hy) in enumerate(history[-8:], 1):
        cv2.circle(img, (hx, hy), 14, (160, 160, 160), 3)
        cv2.putText(
            img,
            str(idx),
            (hx + 16, hy - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (160, 160, 160),
            2,
        )
    cv2.circle(img, (x, y), 35, (0, 0, 255), 5)       # 红圈
    cv2.line(img, (x - 50, y), (x + 50, y), (0, 0, 255), 3)  # 横线
    cv2.line(img, (x, y - 50), (x, y + 50), (0, 0, 255), 3)  # 竖线
    text = f"{label} ({x}, {y})" if label else f"({x}, {y})"
    ty = max(45, y - 60)
    cv2.rectangle(img, (max(0, x - 20), ty - 35), (min(img.shape[1], x + 390), ty + 10), (255, 255, 255), -1)
    cv2.putText(
        img,
        text,
        (max(10, x - 10), ty),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 255),
        3,
    )
    cv2.imwrite(path, img)


def _ui_text(ui_xml: str) -> str:
    """从 uiautomator XML 中提取用户能看到的文字"""
    if not ui_xml:
        return ""
    values = re.findall(r'(?:text|content-desc)="([^"]*)"', ui_xml)
    return "\n".join(html.unescape(v).strip() for v in values if v.strip())


# ── 二维码解码 ──
def decode_qr(path):
    try:
        img = cv2.imread(str(path))
        if img is None:
            return False, "无法读取"
        d = cv2.QRCodeDetector()
        for s in [1.0, 0.5, 0.75, 1.5, 2.0, 3.0, 4.0, 0.25]:
            target = img if s == 1.0 else cv2.resize(
                img, (int(img.shape[1] * s), int(img.shape[0] * s))
            )
            c, _, _ = d.detectAndDecode(target)
            if c:
                return True, c
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for th in [0, 80, 120, 160]:
            if th == 0:
                bin_img = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 11, 2
                )
            else:
                _, bin_img = cv2.threshold(gray, th, 255, cv2.THRESH_BINARY)
            c, _, _ = d.detectAndDecode(bin_img)
            if c:
                return True, c
        return False, "无法识别"
    except Exception as e:
        return False, str(e)

def scan_folder(d):
    d = Path(d)
    if not d.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
    files = sorted([
        f for f in d.iterdir()
        if f.suffix.lower() in exts and not f.name.startswith(".")
    ])
    if not files:
        return []
    log.info(f"找到 {len(files)} 张图片，解码中...")
    res = []
    for i, f in enumerate(files, 1):
        ok, content = decode_qr(f)
        tag, msg = ("✅", f"→ {content[:60]}...") if ok else ("❌", f"→ {content}")
        (log.info if ok else log.warning)(
            f"  [{i}/{len(files)}] {tag} {f.name} {msg}"
        )
        res.append((f, ok, content))
    log.info(f"解码完成: {sum(1 for _, o, _ in res if o)}/{len(files)} 成功")
    return res


# ── 环境检查 ──
def check_environment(backend: DeviceBackend = None):
    print("=" * 50 + "\n🔍 环境检查\n" + "=" * 50)
    issues = []

    # 检查 ADB
    rc, _, _ = AdbBackend._raw_adb(["version"])
    if rc == 0:
        print("✅ ADB 已安装")
    else:
        print("❌ ADB 未安装 → brew install android-platform-tools")
        issues.append("ADB")

    # 检查设备连接
    if backend is None:
        backend = create_backend()

    if backend.is_connected():
        info = backend.get_device_info()
        print(f"✅ 设备已连接: {info.name}")
    else:
        print("❌ 未检测到设备")
        print("   运行 python3 wechat_batch.py pair          # 无线连接")
        print("   运行 python3 wechat_batch.py pair --mode usb  # USB 连接")
        issues.append("设备")

    # 检查微信
    if backend.is_connected():
        if backend.check_wechat_installed():
            print("✅ 微信已安装")
        else:
            print("❌ 未安装微信")

    print(f"✅ OpenCV {cv2.__version__}")
    print("✅ PyYAML 已安装")

    if issues:
        print(f"\n⚠️  {len(issues)} 个问题: {', '.join(issues)}")
    else:
        print("\n🎉 环境就绪！")
        print("   python3 wechat_batch.py scan --dry-run  # 预览")
        print("   python3 wechat_batch.py scan             # 正式跑")
    return len(issues) == 0

# ── 连接设备 ──

def _disconnect_all_wireless():
    """断开所有无线 adb 连接（USB 设备保留）"""
    _, out, _ = AdbBackend._raw_adb(["devices"])
    for line in out.split("\n")[1:]:
        line = line.strip()
        if not line:
            continue
        serial = line.split("\t")[0]
        if ":" in serial or "._adb-tls-connect" in serial:
            AdbBackend._raw_adb(["disconnect", serial])


def _restart_adb_server():
    """重启 ADB server，避免系统 adb 和内置 adb 混用导致协议错误。"""
    print("🔄 正在重启 ADB 服务...")
    AdbBackend._raw_adb(["kill-server"], timeout=8)
    time.sleep(0.5)
    rc, out, err = AdbBackend._raw_adb(["start-server"], timeout=10)
    if rc == 0:
        print("✅ ADB 服务已重启\n")
        return True
    print("⚠️ ADB 服务启动失败")
    if err or out:
        print(f"   {err or out}")
    print("   可尝试关闭其他 Android 工具后重新打开本软件。")
    return False


def _print_adb_pair_error(out: str, err: str):
    msg = err or out or "未知错误"
    print(f"❌ 配对失败: {msg}")
    if "protocol fault" in msg or "couldn't read status message" in msg:
        print("\n   这个错误通常是 ADB 服务状态混乱或配对端口过期。")
        print("   请按下面顺序重试：")
        print("   1. 关闭手机「无线调试」，再重新打开")
        print("   2. 重新点击「使用配对码配对设备」")
        print("   3. 输入新弹窗里的配对 IP:端口和配对码")
        print("   4. 配对成功后，返回无线调试主页，再输入主页里的连接 IP:端口")
        print("   注意：配对弹窗里的端口和主页连接端口通常不是同一个。")


def guided_pair_usb():
    """USB 连接：自动检测设备并确认"""
    print("=" * 50 + "\n🔌 USB 连接设备\n" + "=" * 50)
    print("📱 第一次 USB 连接请按这个顺序做：")
    print("   1. 用支持数据传输的 USB 线连接手机和电脑")
    print("      提醒：有些线只能充电，不能传数据，连不上时优先换线")
    print("   2. 手机打开：设置 → 关于手机 → 连续点击「版本号」7 次")
    print("      看到「已处于开发者模式」即可")
    print("   3. 手机打开：设置 → 开发者选项 → 开启「USB 调试」")
    print("   4. 华为/荣耀建议同时开启「仅充电模式下允许 ADB 调试」")
    print("   5. 手机弹出「允许 USB 调试」时，勾选「一律允许」并点确定")
    print("   6. 如果手机询问 USB 用途，选择「传输文件」或保持连接即可\n")
    input("准备好后按 Enter 开始检测 USB 设备...")

    _restart_adb_server()
    backend = AdbBackend(mode="usb")

    # 先断开无线连接（避免串扰）
    _disconnect_all_wireless()

    if backend.connect():
        info = backend.get_device_info()
        print(f"\n✅ 已连接: {info.name}")
        print(f"   分辨率: {info.screen_w}x{info.screen_h}")
        print(f"   序列号: {backend.get_serial()}")
        print("\n💡 提示: USB 连接稳定不掉线，适合长时间批量操作。")
    else:
        print("\n❌ 未检测到 USB 设备")
        print("   常见排查：")
        print("   • USB 线是否支持数据传输（不是只充电的线）")
        print("   • 手机是否弹出「允许 USB 调试」弹窗未点击")
        print("   • 华为手机：开发者选项 → 打开「仅充电模式下允许 ADB 调试」")
        print("   • 拔掉 USB 线重新插入，再看手机是否弹出授权窗口")
        print("   • 仍不行可在终端尝试：adb kill-server && adb start-server")


def guided_pair():
    """无线配对连接（原有逻辑）"""
    _restart_adb_server()

    # 显示当前连接
    backend = AdbBackend(mode="wireless")
    existing = backend._list_devices()

    if existing:
        print("=" * 50 + "\n📱 当前已连接设备:\n" + "=" * 50)
        for serial, dtype in existing:
            name = "?"
            try:
                tmp = AdbBackend(mode="wireless")
                tmp._serial = serial
                name = tmp.get_device_name()
            except Exception:
                pass
            print(f"   {serial}  ({dtype})  {name}")
        print()
        ans = input("断开以上设备并连接新的？(y/n, 默认y): ").strip().lower()
        if ans != 'n':
            _disconnect_all_wireless()
            print("✅ 已断开所有旧设备\n")
        else:
            print("保持现有连接\n")
            return

    print("=" * 50 + "\n🔗 无线连接设备\n" + "=" * 50)
    print("📱 无线连接前请确认：")
    print("   1. 手机和电脑在同一个 Wi-Fi")
    print("   2. 手机打开：设置 → 开发者选项 → 无线调试")
    print("   3. 如果已经配对过，直接看无线调试主页里的「IP 地址和端口」")
    print("      格式类似：192.168.1.23:37099")
    print("   4. 如果第一次无线连接，先直接按 Enter，程序会进入配对流程\n")

    # 直接连接
    conn = input("连接 IP:端口（已配对过才填；第一次无线连接直接按 Enter）: ").strip()
    if conn:
        print(f"连接 {conn} ...")
        rc, out, _ = AdbBackend._raw_adb(["connect", conn], timeout=15)
        if rc == 0 and "connected" in out:
            # 断开残留的 TLS 连接
            for serial, _ in backend._list_devices():
                if "._adb-tls-connect" in serial:
                    AdbBackend._raw_adb(["disconnect", serial])
            backend._serial = conn
            name = backend.get_device_name()
            print(f"✅ 已连接: {name}\n")
            return
        print(f"连接失败，可能需要配对: {out}\n")

    # 配对
    print("📱 第一次无线连接需要先配对：")
    print("   1. 手机保持在「无线调试」页面")
    print("   2. 点「使用配对码配对设备」")
    print("   3. 屏幕会显示「配对码」和「IP 地址和端口」")
    print("   4. 先输入这个弹窗里的配对 IP:端口，再输入配对码\n")
    info = input("配对 IP:端口（配对码弹窗里的地址）: ").strip()
    code = input("配对码（6 位数字）: ").strip()
    if not info or not code:
        print("❌ 输入无效")
        return
    rc, out, err = AdbBackend._raw_adb(["pair", info, code], timeout=15)
    if rc != 0 or "Successfully" not in out:
        _print_adb_pair_error(out, err)
        return
    print("✅ 配对成功！")
    print("📱 现在请回到无线调试主页，不要再用配对弹窗里的端口")
    print("   找主页显示的「IP 地址和端口」，格式类似 192.168.1.23:37099")
    conn2 = input("连接 IP:端口（无线调试主页里的地址）: ").strip()
    if not conn2:
        return
    rc, out, _ = AdbBackend._raw_adb(["connect", conn2], timeout=15)
    if rc == 0 and "connected" in out:
        for serial, _ in backend._list_devices():
            if "._adb-tls-connect" in serial:
                AdbBackend._raw_adb(["disconnect", serial])
        backend._serial = conn2
        name = backend.get_device_name()
        print(f"✅ 已连接: {name}")
    else:
        print(f"❌ 失败: {out}")
        if "protocol fault" in out:
            print("   请关闭并重新打开手机无线调试，然后重新配对。")

# ── 逐步校准 ──
def calibrate(backend: DeviceBackend = None):
    if backend is None:
        backend = create_backend()
    if not backend.is_connected():
        print("❌ 设备未连接")
        return

    import numpy as np

    def cleanup_debug_images():
        """删除校准时生成的临时定位截图，保留坐标 YAML。"""
        screenshot_dir = SCRIPT_DIR / "coords_export"
        removed = 0
        for path in screenshot_dir.glob("debug_*.png"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    cleanup_debug_images()

    screen_w, screen_h = backend.get_screen_size()
    print(f"📱 屏幕: {screen_w}x{screen_h}\n")
    config = load_config()

    def ask_tap(label, est_x_pct, est_y_pct):
        """先截图预览，再由用户决定是否测试点击。"""
        tx, ty = int(screen_w * est_x_pct), int(screen_h * est_y_pct)
        origin_x, origin_y = tx, ty
        step = 10
        history = []
        screenshot_dir = SCRIPT_DIR / "coords_export"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        before_path = str(screenshot_dir / "debug_before_tap.png")
        after_path = str(screenshot_dir / "debug_after_tap.png")

        def cleanup_step_images():
            removed = 0
            for path in (Path(before_path), Path(after_path)):
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            if removed:
                print(f"  🧹 已删除 {removed} 张本步骤定位截图")

        def preview(path=before_path, title="预览"):
            backend.screenshot(path)
            _mark_screenshot(path, tx, ty, history, label)
            subprocess.run(["open", "-g", path])
            print(f"\n  📸 {title}已打开: {Path(path).name}（红圈=当前点，灰圈=历史点）")

        def test_tap():
            print(f"\n  👆 测试点击 ({tx}, {ty}) ...")
            preview(before_path, "点击前截图")
            history.append((tx, ty))
            backend.tap(tx, ty)
            time.sleep(0.8)
            backend.screenshot(after_path)
            _mark_screenshot(after_path, tx, ty, history[:-1], label)
            subprocess.run(["open", "-g", after_path])
            print(f"  📸 点击后截图已打开: {Path(after_path).name}")

        print(f"\n  目标: {label} → ({tx}, {ty})")
        print("  先打开截图给你看，不会立即点击手机。")
        preview()
        print("  ────────────────────────────────────")
        print("  p  预览当前位置（只截图，不点击）")
        print("  Enter  直接点击并确认，进入下一步")
        print("  t  测试点击，并打开点击前/点击后截图")
        print("  a  打开上一次点击后截图")
        print("  ↑↓←→  微调坐标        ⇧+方向键  大幅微调")
        print("  +/-  增减步长          直接输 x y  跳转坐标")
        print("  y  确认保存当前位置    q  放弃并保留初始点")
        print("  ────────────────────────────────────")

        while True:
            dx, dy = tx - origin_x, ty - origin_y
            off = f"({'+' if dx >= 0 else ''}{dx}, {'+' if dy >= 0 else ''}{dy})"
            print(f"\r  📍 {label} ({tx}, {ty}) 偏移:{off} 步长:{step}px  ", end='', flush=True)
            key = _getch()

            moved = False
            if key == 'UP':
                ty = max(0, ty - step)
                moved = True
            elif key == 'DOWN':
                ty = min(screen_h, ty + step)
                moved = True
            elif key == 'LEFT':
                tx = max(0, tx - step)
                moved = True
            elif key == 'RIGHT':
                tx = min(screen_w, tx + step)
                moved = True
            elif key == 'S-UP':
                ty = max(0, ty - step * 3)
                moved = True
            elif key == 'S-DOWN':
                ty = min(screen_h, ty + step * 3)
                moved = True
            elif key == 'S-LEFT':
                tx = max(0, tx - step * 3)
                moved = True
            elif key == 'S-RIGHT':
                tx = min(screen_w, tx + step * 3)
                moved = True
            elif key in ('p', 'P'):
                preview()
            elif key == 'ENTER':
                print(f"\n  ✅ 已确认并点击 ({tx}, {ty})，进入下一步")
                history.append((tx, ty))
                backend.tap(tx, ty)
                time.sleep(0.8)
                cleanup_step_images()
                return tx / screen_w, ty / screen_h
            elif key in ('t', 'T'):
                test_tap()
                ok = input("  这次点对了吗？(y/n，默认 n): ").strip().lower()
                if ok == 'y':
                    cleanup_step_images()
                    return tx / screen_w, ty / screen_h
                print("  继续根据截图微调，按 p 可重新预览。")
            elif key in ('a', 'A'):
                if Path(after_path).exists():
                    subprocess.run(["open", "-g", after_path])
                    print(f"\n  📷 已打开点击后截图: {Path(after_path).name}")
                else:
                    print("\n  ⚠️ 还没有点击后截图，请先按 t 测试点击")
            elif key == '+':
                step = min(step * 2, 80)
                print(f"\n  步长 → {step}px")
            elif key == '-':
                step = max(step // 2, 1)
                print(f"\n  步长 → {step}px")
            elif key in ('y', 'Y'):
                print()
                cleanup_step_images()
                return tx / screen_w, ty / screen_h
            elif key in ('q', 'Q', 'ESC'):
                print(f"\n  ⚠️ 放弃本次调整，保留初始点 ({origin_x}, {origin_y})")
                return origin_x / screen_w, origin_y / screen_h
            else:
                sys.stdout.write(key)
                sys.stdout.flush()
                rest = ''
                while True:
                    c = _getch()
                    if c == 'ENTER':
                        break
                    if c == 'ESC':
                        rest = ''
                        break
                    if len(c) == 1:
                        sys.stdout.write(c)
                        sys.stdout.flush()
                        rest += c
                    else:
                        break
                inp = (key + rest).strip()
                ps = inp.split()
                if len(ps) == 2:
                    try:
                        tx = max(0, min(screen_w, int(ps[0])))
                        ty = max(0, min(screen_h, int(ps[1])))
                        moved = True
                        print(f"\n  跳转到 ({tx}, {ty})")
                    except ValueError:
                        print(f"\n  ⚠️ 无效输入: {inp}")
                else:
                    print(f"\n  ⚠️ 无效输入: {inp}")

            if moved:
                print(f"\n  已移动到 ({tx}, {ty})，按 p 预览或按 t 测试点击。")

    # 不强制打开微信，用户自己打开
    print("📱 请手动打开微信到聊天列表主页，按 Enter 开始...")
    input()

    # 1. +
    print("\n" + "=" * 50 + "\n📌 1/5: 「+」按钮\n" + "=" * 50)
    print("在聊天列表主页，确认 + 的位置")
    x1, y1 = ask_tap("➕", 0.985, 0.055)

    # 2. 扫一扫
    print("\n" + "=" * 50 + "\n📌 2/5: 「扫一扫」\n" + "=" * 50)
    print("请手动点 + 打开菜单，确认「扫一扫」的位置")
    x2, y2 = ask_tap("📷 扫一扫", 0.80, 0.267)

    # 3. 相册
    print("\n" + "=" * 50 + "\n📌 3/5: 「相册」\n" + "=" * 50)
    print("请手动进入扫一扫界面，确认「相册」的位置")
    x3, y3 = ask_tap("🖼️  相册", 0.89, 0.89)

    # 4. 选图（用分割线自动识别第一行第二列）
    print("\n" + "=" * 50 + "\n📌 4/5: 「选图」位置\n" + "=" * 50)
    print("请手动进入相册界面，按 Enter 自动分析网格线...")
    input()
    x4, y4 = (0.50, 0.32)  # 默认值
    print("正在分析网格线...")

    # 截图分析网格
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    est_x, est_y = x4, y4
    try:
        backend.screenshot(tmp.name)
        grid_img = cv2.imread(tmp.name)
        if grid_img is None:
            raise RuntimeError("截图文件不可读取，请确认 ADB 截图正常、手机没有断连")

        gh, gw = grid_img.shape[:2]
        gray = cv2.cvtColor(grid_img, cv2.COLOR_BGR2GRAY)

        # 找横向分割线
        row_avg = np.mean(gray, axis=1)
        row_diff = np.abs(np.diff(row_avg))
        threshold = np.percentile(row_diff, 95)
        row_lines = np.where(row_diff > threshold)[0]
        clusters = []
        if len(row_lines) > 0:
            cur = [row_lines[0]]
            for r in row_lines[1:]:
                if r - cur[-1] < 20:
                    cur.append(r)
                else:
                    clusters.append(int(np.mean(cur)))
                    cur = [r]
            clusters.append(int(np.mean(cur)))

        # 找纵向分割线
        col_avg = np.mean(gray, axis=0)
        col_diff = np.abs(np.diff(col_avg))
        threshold_c = np.percentile(col_diff, 95)
        col_lines = np.where(col_diff > threshold_c)[0]
        col_clusters = []
        if len(col_lines) > 0:
            cur = [col_lines[0]]
            for c in col_lines[1:]:
                if c - cur[-1] < 20:
                    cur.append(c)
                else:
                    col_clusters.append(int(np.mean(cur)))
                    cur = [c]
            col_clusters.append(int(np.mean(cur)))

        # 推算单元格中心
        row_edges = [0] + sorted(clusters) + [gh]
        col_edges = [0] + sorted(col_clusters) + [gw]

        row0_start = row_edges[0]
        row0_end = row_edges[1] if len(row_edges) > 1 else gh
        col1_start = col_edges[1] if len(col_edges) > 2 else gw // 3
        col1_end = col_edges[2] if len(col_edges) > 2 else 2 * gw // 3

        est_x = (col1_start + col1_end) / 2 / gw
        est_y = (row0_start + row0_end) / 2 / gh
        print(f"  🎯 自动识别: 第1行第2列 → ({int(gw * est_x)}, {int(gh * est_y)})")
    except Exception as e:
        print(f"  ⚠️ 自动分析失败: {e}")
        print("  将使用默认估算点继续，请在下一步截图里手动微调。")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    x4, y4 = ask_tap("🖱️  选图", est_x, est_y)

    # 5. 加入群聊
    print("\n" + "=" * 50 + "\n📌 5/5: 「加入群聊」绿色按钮\n" + "=" * 50)
    print("请手动选一张群二维码图片，看到「加入群聊」按钮后按 Enter...")
    input()
    x5, y5 = ask_tap("👥 加入群聊", 0.50, 0.836)

    config["coords"] = {
        "plus": {"x": x1, "y": y1},
        "scan": {"x": x2, "y": y2},
        "album": {"x": x3, "y": y3},
        "image": {"x": x4, "y": y4},
        "join_group": {"x": x5, "y": y5},
        "screen_w": screen_w,
        "screen_h": screen_h,
    }
    save_config(config)

    # ── 导出坐标文件 ──
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_name = f"coords_{backend.get_device_name()}_{screen_w}x{screen_h}_{now}.yaml"
    export_path = SCRIPT_DIR / "coords_export" / export_name
    export_data = {
        "device": backend.get_device_name(),
        "screen": f"{screen_w}x{screen_h}",
        "date": datetime.now().isoformat(),
        "coords": config["coords"],
    }
    with open(export_path, "w", encoding="utf-8") as f:
        yaml.dump(export_data, f, allow_unicode=True, default_flow_style=False)
    print(f"\n📁 坐标已导出: coords_export/{export_name}")

    removed = cleanup_debug_images()
    if removed:
        print(f"🧹 已自动删除 {removed} 张临时定位截图")

    print("\n✅ 全部校准完成！坐标已保存到 config.yaml")

# ── 核心：加群 ──
FAILURE_KEYWORDS = [
    "二维码已过期",
    "该二维码已过期",
    "无法加入",
    "群聊人数已满",
    "操作频繁",
    "请稍后再试",
    "邀请已失效",
    "群聊不存在",
    "你无法加入",
    "该群因违规",
]

PENDING_KEYWORDS = [
    "加入群聊",
    "加入该群聊",
]


def judge_join_result(ui_xml: str):
    """根据当前界面文字判断加群结果，返回 (ok, message, ui_text)。"""
    text = _ui_text(ui_xml)
    if not text:
        return None, "无法读取当前界面文字", ""

    for kw in FAILURE_KEYWORDS:
        if kw in text:
            return False, f"发现失败提示: {kw}", text

    for kw in PENDING_KEYWORDS:
        if kw in text:
            return None, f"仍停留在「{kw}」页面", text

    return True, "未发现失败提示，且已离开加入按钮页面", text


def save_diagnostic_screenshot(backend: DeviceBackend, image_name: str, config, reason: str):
    """失败或可疑时截图留证，便于复盘为什么没加上。"""
    runtime = config.get("_runtime", {})
    screenshot_dir = runtime.get("screenshot_dir")
    if not screenshot_dir:
        return ""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(image_name).stem)
    safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", reason)[:40] or "diagnostic"
    path = Path(screenshot_dir) / f"{datetime.now().strftime('%H%M%S')}_{safe_name}_{safe_reason}.png"
    try:
        backend.screenshot(str(path))
        return str(path)
    except Exception as e:
        log.debug(f"截图失败: {e}")
        return ""


def join_group(image_path, group_url, config, backend: DeviceBackend):
    coords = config.get("coords", {})
    gc = lambda k, dx, dy: (
        coords.get(k, {}).get("x", dx),
        coords.get(k, {}).get("y", dy),
    )
    pxp, pyp = gc("plus", 0.985, 0.055)
    sxp, syp = gc("scan", 0.80, 0.267)
    axp, ayp = gc("album", 0.89, 0.89)
    ixp, iyp = gc("image", 0.50, 0.32)
    jxp, jyp = gc("join_group", 0.50, 0.836)
    sw, sh = backend.get_screen_size()
    ui_cfg = config.get("ui", {})
    jw = ui_cfg.get("join_wait", 3)
    max_wait = ui_cfg.get("max_wait", 10)
    # 推送图片到设备
    rp = f"/sdcard/DCIM/Camera/qr_{int(time.time())}.jpg"
    backend.push_file(str(image_path), rp)
    if backend.set_file_mtime(rp):
        log.info("  🕒 手机图片时间已校准为设备当前时间")
    else:
        log.debug("手机图片时间校准失败，继续执行媒体扫描")
    backend.media_scan(rp)

    # 操作序列：+ → 扫一扫 → 相册 → 选图 → 加入群聊 → 返回
    for label, xp, yp, w in [
        ("➕", pxp, pyp, 0.8),
        ("📷", sxp, syp, 1.5),
        ("🖼️", axp, ayp, 1.5),
    ]:
        tx, ty = int(sw * xp), int(sh * yp)
        log.info(f"  {label} ({tx},{ty})")
        backend.tap(tx, ty)
        time.sleep(w)

    log.info("  ⏳ 等相册...")
    time.sleep(2)
    tx, ty = int(sw * ixp), int(sh * iyp)
    log.info(f"  🖱️ 选图 ({tx},{ty})")
    backend.tap(tx, ty)
    time.sleep(3)

    time.sleep(1)
    tx, ty = int(sw * jxp), int(sh * jyp)
    log.info(f"  👥 加入 ({tx},{ty})")
    backend.tap(tx, ty)
    time.sleep(jw)

    ok, msg, ui_text = None, "尚未判断", ""
    deadline = time.time() + max(0, max_wait - jw)
    while True:
        ui_xml = backend.dump_ui()
        ok, msg, ui_text = judge_join_result(ui_xml)
        if ok is not None or time.time() >= deadline:
            break
        time.sleep(1)

    screenshot = ""
    if ok is not True:
        screenshot = save_diagnostic_screenshot(backend, image_path.name, config, msg)

    log.info("  ↩️ 返回")
    backend.back()
    time.sleep(0.5)
    meta = {
        "ui_text": ui_text,
        "screenshot": screenshot,
        "group_url": group_url,
    }
    if ok is True:
        return True, msg, meta
    if ok is False:
        return False, msg, meta
    return False, f"结果可疑: {msg}", meta

# ── 批量主流程 ──
def get_today_count(log_dir):
    lf = (
        _path(log_dir)
        if isinstance(log_dir, str)
        else Path(log_dir)
    ) / f"scan_{datetime.now().strftime('%Y-%m-%d')}.log"
    if not lf.exists():
        return 0
    markers = ("加群成功", "已点击加入")
    return sum(1 for line in open(lf, encoding="utf-8") if any(m in line for m in markers))

def _path(p_str, default="."):
    """解析路径：相对路径基于 SCRIPT_DIR"""
    pp = Path(p_str) if p_str else Path(default)
    return pp if pp.is_absolute() else SCRIPT_DIR / pp


def write_run_report(report_dir: Path, records: list[dict], dry_run: bool):
    """生成 CSV + Markdown 报告，方便跑完后快速复盘。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"report_{run_id}.csv"
    md_path = report_dir / f"report_{run_id}.md"

    fields = ["time", "file", "status", "message", "url", "screenshot"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in fields})

    total = len(records)
    success = sum(1 for r in records if r.get("status") == "success")
    failed = sum(1 for r in records if r.get("status") == "failed")
    invalid = sum(1 for r in records if r.get("status") == "invalid")
    suspicious = sum(1 for r in records if r.get("status") == "suspicious")
    decoded = sum(1 for r in records if r.get("status") == "decoded")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 微信批量扫码运行报告\n\n")
        f.write(f"- 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 模式: {'预览模式' if dry_run else '正式模式'}\n")
        f.write(f"- 总数: {total}\n")
        f.write(f"- 成功: {success}\n")
        f.write(f"- 失败: {failed}\n")
        f.write(f"- 可疑: {suspicious}\n")
        f.write(f"- 预览可识别: {decoded}\n")
        f.write(f"- 无法解码: {invalid}\n\n")
        f.write("| 文件 | 状态 | 原因 | 截图 |\n")
        f.write("|---|---|---|---|\n")
        for rec in records:
            shot = rec.get("screenshot") or ""
            f.write(
                f"| {rec.get('file', '')} | {rec.get('status', '')} | "
                f"{rec.get('message', '').replace('|', '/')} | {shot} |\n"
            )

    return csv_path, md_path

def run_batch(config, backend: DeviceBackend = None):
    p = config.get("paths", {})
    s = config.get("scan", {})
    r = config.get("rate_limit", {})

    inp = _path(p.get("input_dir"), "data/qr_input")
    okd = _path(p.get("processed_dir"), "data/qr_processed")
    bd = _path(p.get("failed_dir"), "data/qr_failed")
    cl = _path(p.get("content_log"), "data/qr_content/urls.txt")
    ld = _path(p.get("log_dir"), "logs")
    report_dir = _path(p.get("report_dir"), "logs/reports")
    screenshot_root = _path(p.get("screenshot_dir"), "logs/screenshots")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_dir = screenshot_root / run_id
    for d in [okd, bd, cl.parent, ld, report_dir, screenshot_dir]:
        d.mkdir(parents=True, exist_ok=True)
    config["_runtime"] = {
        "screenshot_dir": str(screenshot_dir),
        "run_id": run_id,
    }

    dry = s.get("dry_run", False)
    records = []

    # ── 创建设备后端 ──
    if backend is None:
        dev_cfg = config.get("device", {})
        backend = create_backend(
            device_type=dev_cfg.get("type", "auto"),
            mode=dev_cfg.get("android_mode", "auto"),
        )

    if not dry:
        if not backend.is_connected():
            log.error("❌ 设备未连接！")
            log.error("   python3 wechat_batch.py pair              # 无线连接")
            log.error("   python3 wechat_batch.py pair --mode usb   # USB 连接")
            return
        info = backend.get_device_info()
        log.info(f"📱 设备: {info.name}  ({info.backend_type})")

    log.info("=" * 50 + "\n📷 解码...\n" + "=" * 50)
    results = scan_folder(inp)
    if not results:
        log.info("无图片")
        return

    valid = [(p, c) for p, ok, c in results if ok]
    invalid = [(p, c) for p, ok, c in results if not ok]
    log.info(f"成功: {len(valid)} | 失败: {len(invalid)}")

    for pth, reason in invalid:
        records.append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "file": pth.name,
            "status": "invalid",
            "message": reason,
            "url": "",
            "screenshot": "",
        })
        if not dry:
            shutil.move(str(pth), str(bd / pth.name))
    with open(cl, "a", encoding="utf-8") as f:
        for _, c in valid:
            f.write(f"{datetime.now().isoformat()}\t{c}\n")

    if dry:
        for pth, url in valid:
            records.append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "file": pth.name,
                "status": "decoded",
                "message": "二维码可识别，预览模式未操作手机",
                "url": url,
                "screenshot": "",
            })
        log.info("🔍 预览结束（未操作手机）")
        csv_path, md_path = write_run_report(report_dir, records, dry)
        log.info(f"📄 报告: {md_path}")
        return

    remain = r.get("daily_limit", 150) - get_today_count(ld)
    if remain <= 0:
        log.warning(f"⚠️ 今日已达上限")
        return
    if len(valid) > remain:
        log.warning(f"⚠️ 今日剩余 {remain}，只处理前 {remain} 个")
        valid = valid[:remain]

    log.info("=" * 50 + f"\n🤖 加群 ({len(valid)} 个)...\n" + "=" * 50)
    sc, fc = 0, 0
    for i, (pth, url) in enumerate(valid, 1):
        log.info(f"\n[{i}/{len(valid)}] {pth.name}")
        ok, msg, meta = join_group(pth, url, config, backend)
        if ok:
            log.info(f"  ✅ 加群成功: {msg}")
            sc += 1
            status = "success"
            shutil.move(str(pth), str(okd / pth.name))
        else:
            log.warning(f"  ❌ {msg}")
            fc += 1
            status = "failed" if not msg.startswith("结果可疑") else "suspicious"
            shutil.move(str(pth), str(bd / pth.name))
        records.append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "file": pth.name,
            "status": status,
            "message": msg,
            "url": url,
            "screenshot": meta.get("screenshot", ""),
        })
        if i % r.get("batch_size", 50) == 0 and i < len(valid):
            log.info(f"\n⏸️  {i}/{len(valid)} ✅{sc} ❌{fc}  按 Enter 继续...")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                break
        if i < len(valid) and ok:
            w = random.randint(
                r.get("min_interval", 20), r.get("max_interval", 60)
            )
            log.info(f"  ⏳ {w}s")
            time.sleep(w)

    log.info(
        f"\n{'='*50}\n"
        f"📊 解码:{len(valid)} 加群:✅{sc} ❌{fc} 今日:{get_today_count(ld)}\n"
        f"{'='*50}"
    )
    csv_path, md_path = write_run_report(report_dir, records, dry)
    log.info(f"📄 报告: {md_path}")

# ── CLI ──
def safety():
    print("""\n┌──────────────────────────────────────┐
│           🛡️  使用前注意               │
│ 📱 微信已登录，屏幕不锁               │
│ 🆘 Ctrl+C 紧急停止                    │
│ ⚠️ 运行时不操作手机                   │
└──────────────────────────────────────┘""")

def main():
    p = argparse.ArgumentParser(description="微信批量扫码加群插件")
    sp = p.add_subparsers(dest="cmd")

    sp.add_parser("setup", help="检查环境")

    pr = sp.add_parser("pair", help="连接设备（无线 / USB）")
    pr.add_argument(
        "--mode", "-m",
        choices=["usb", "wireless", "auto"],
        default="wireless",
        help="连接模式: usb / wireless / auto（默认 wireless）",
    )

    sp.add_parser("calibrate", help="换手机校准按钮")

    sp.add_parser("retry", help="失败图片移回 qr_input")

    s2 = sp.add_parser("scan", help="批量扫码加群")
    s2.add_argument("-i", "--input")
    s2.add_argument("-c", "--config")
    s2.add_argument("--dry-run", action="store_true")
    s2.add_argument(
        "--mode", "-m",
        choices=["usb", "wireless", "auto"],
        default=None,
        help="连接模式（覆盖 config.yaml 设置）",
    )
    s2.add_argument(
        "--device", "-d",
        choices=["android", "auto"],
        default=None,
        help="设备类型（覆盖 config.yaml 设置）",
    )

    args = p.parse_args()

    if not args.cmd:
        p.print_help()
        safety()
        return

    # ── pair 命令（连接设备） ──
    if args.cmd == "pair":
        mode = getattr(args, "mode", "wireless")
        if mode == "usb":
            guided_pair_usb()
        else:
            guided_pair()
        return

    # ── 其他命令：加载配置 & 初始化日志 ──
    config = load_config(getattr(args, "config", None))
    global log
    log, lf = setup_logging(
        str(_path(config.get("paths", {}).get("log_dir"), "logs"))
    )

    if args.cmd == "setup":
        # 检查环境：尝试用配置中的模式连接
        dev_cfg = config.get("device", {})
        backend = create_backend(
            device_type=dev_cfg.get("type", "auto"),
            mode=dev_cfg.get("android_mode", "auto"),
        )
        # 不强制连接，只检测
        check_environment(backend)

    elif args.cmd == "calibrate":
        dev_cfg = config.get("device", {})
        backend = create_backend(
            device_type=dev_cfg.get("type", "auto"),
            mode=dev_cfg.get("android_mode", "auto"),
        )
        if not backend.is_connected():
            log.error("❌ 设备未连接，请先运行: python3 wechat_batch.py pair")
            return
        calibrate(backend)

    elif args.cmd == "retry":
        bd = _path(config.get("paths", {}).get("failed_dir"), "data/qr_failed")
        inp = _path(config.get("paths", {}).get("input_dir"), "data/qr_input")
        fs = list(bd.glob("*")) if bd.exists() else []
        for f in fs:
            if f.is_file() and not f.name.startswith("."):
                shutil.move(str(f), str(inp / f.name))
                print(f"  ↩️ {f.name}")
        print(f"✅ {len(fs)} 个文件移回 qr_input")

    elif args.cmd == "scan":
        safety()

        if args.input:
            config.setdefault("paths", {})["input_dir"] = args.input
        if args.dry_run:
            config.setdefault("scan", {})["dry_run"] = True

        # 创建后端
        dev_cfg = config.get("device", {})
        device_type = args.device or dev_cfg.get("type", "auto")
        mode = args.mode or dev_cfg.get("android_mode", "auto")
        backend = create_backend(device_type=device_type, mode=mode)

        try:
            run_batch(config, backend)
        except KeyboardInterrupt:
            log.info("\n👋 已停止")
        except Exception as e:
            log.error(f"\n❌ {e}")
            import traceback
            log.debug(traceback.format_exc())

if __name__ == "__main__":
    os.chdir(SCRIPT_DIR)  # 自动切到脚本所在目录
    main()
