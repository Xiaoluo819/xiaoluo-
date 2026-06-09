# Agent Handoff Notes

本文件用于后续展开新对话时快速理解当前项目。项目路径：

```text
/Users/shizd/Documents/wechat scan
```

## 项目概览

这是“微信批量扫码加群 v2.5 Codex 版”。核心目标是通过 Android ADB 控制手机微信，批量读取 `data/qr_input/` 里的群二维码图片，进入微信扫一扫、从相册选图、点击“加入群聊”，并把结果写入日志和报告。

当前 v2.5 包体位于：

```text
/Users/shizd/Documents/wechat scan/v2.5-codex/
```

项目目前主要是 Python 脚本工具，不是 Web 应用。

## 关键文件

```text
wechat_batch.py          主程序和 CLI 入口
config.yaml              设备模式、坐标、路径、限速等配置
requirements.txt         Python 依赖：opencv-python、pyyaml
启动.sh                  交互式菜单启动脚本
启动.command             macOS 双击启动版
使用手册.md              完整中文操作说明
CODEX-v2.5说明.md        v2.5 改进点说明
backends/base.py         设备后端抽象接口
backends/adb.py          Android ADB 后端，支持 USB / wireless / auto
coords_export/           校准坐标导出目录
data/qr_input/           待处理二维码图片目录
data/qr_processed/       成功后移动到这里
data/qr_failed/          失败后移动到这里
data/qr_application_required/ 需要发送入群申请的图片移动到这里
data/qr_content/         解码出的群链接记录
logs/                    日志、报告、失败/可疑截图
templates/               模板截图目录，当前不强依赖
tools/platform-tools/    内置 macOS Android Platform Tools，优先使用这里的 adb
```

注意：`使用手册.md` 里提到 `README.md`，但当前仓库根目录没有 `README.md`。

## 运行方式

推荐从菜单启动：

```bash
bash 启动.sh
```

也可以直接运行 CLI：

```bash
python3 wechat_batch.py setup
python3 wechat_batch.py pair --mode usb
python3 wechat_batch.py pair --mode wireless
python3 wechat_batch.py calibrate
python3 wechat_batch.py scan --dry-run
python3 wechat_batch.py scan
python3 wechat_batch.py retry
```

依赖安装：

```bash
pip3 install -r requirements.txt
```

项目已内置 macOS ADB，默认优先使用：

```text
tools/platform-tools/adb
```

如果内置 ADB 不存在，再考虑安装系统 ADB。

## 已移除功能

v2.5 已清理废弃的电脑微信图片导入功能。后续不要恢复相关命令、菜单入口或目录配置。

## 当前配置要点

`config.yaml` 当前保存的是一台 `1264x2800` 设备的比例坐标，包含：

- `plus`
- `scan`
- `album`
- `image`
- `join_group`

设备连接配置里当前是：

```yaml
device:
  emulator_ports:
  - 7555
  - 22471
  - 5555
  mode: auto
```

但主程序读取时主要看 `device.type` 和 `device.android_mode`，如果没有就默认 `auto`。后续如果整理配置，注意这里可能有命名不一致的问题。

限速配置：

```yaml
rate_limit:
  min_interval: 180
  max_interval: 300
  daily_limit: 150
  batch_size: 50
```

## 程序行为

主流程在 `wechat_batch.py`：

1. 解码 `data/qr_input/` 里的图片二维码。
2. 通过 ADB 把图片推送到手机。
3. 触发媒体扫描，让手机图库能看到图片。
4. 按配置坐标依次点击微信：`+`、扫一扫、相册、图片、“加入群聊”。
5. 通过 `uiautomator dump` 读取当前 UI 文本，判断成功、失败或可疑。
6. 成功图片移动到 `data/qr_processed/`，失败/可疑图片移动到 `data/qr_failed/`。
7. 需要发送入群申请的图片移动到 `data/qr_application_required/`，程序返回微信主界面继续下一张。
8. 写入日志、CSV/Markdown 报告，失败、可疑或需申请时截图。

状态含义：

- `success`：判断成功。
- `failed`：明确失败。
- `suspicious`：结果可疑，需要看截图确认。
- `application_required`：需要发送入群申请，已跳过并单独归档。
- `invalid`：图片无法识别二维码。
- `decoded`：预览模式下二维码可识别，但没有操作手机。

## 开发注意事项

- 不要轻易改动坐标格式；当前坐标是按屏幕比例保存，运行时根据设备分辨率换算。
- 这是基于真实手机屏幕坐标点击的工具，微信 UI、手机分辨率、弹窗、网络延迟都会影响结果。
- 修改 ADB 行为时优先看 `backends/adb.py`，不要把设备细节散落到主流程里。
- 修改批量流程、报告、状态判断时优先看 `wechat_batch.py`。
- 运行正式 `scan` 会操作真实微信和移动图片文件；测试代码时优先用 `scan --dry-run`。
- 不要把二维码图片、失败截图、日志报告误删。若要清理，请先征得用户确认。
- 当前 Git 状态里这些项目文件是新加入的未跟踪文件，用户尚未要求提交。

## 常见后续任务入口

- 想增强识别/判断结果：看 `join_group`、`_ui_text`、报告记录逻辑。
- 想改连接设备体验：看 `pair` 命令、`guided_pair*` 函数和 `backends/adb.py`。
- 想改校准体验：看 `calibrate`、`_mark_screenshot`、单键输入 `_getch`。
- 想改文件归档/重试：看 `run_batch` 和 `retry` 分支。
- 想加 iOS 支持：需要实现新的 `DeviceBackend`，当前 `backends/base.py` 已有抽象接口。
