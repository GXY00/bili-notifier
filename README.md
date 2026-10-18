# Bilibili 关注动态桌面监控

这是一个本地 PySide6 桌面程序：登录 B 站后读取完整关注列表，选择需要监控的 UP 主，程序按间隔检查新视频、图文和转发动态，也可以手动立即检查。程序支持系统托盘通知和 Windows 当前用户开机自启。

## 安装

要求 Python 3.9 或更高版本。在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

建议使用项目自己的 `.venv`，避免和系统中其他 Python 工具发生依赖冲突。

## 启动

```powershell
.\.venv\Scripts\python.exe bilibili_followed_dynamics.py
```

也可以双击 `start.bat`。程序会打开桌面窗口。首次运行或 Cookie 失效时，二维码会显示在窗口中，使用哔哩哔哩 App 扫码确认即可。

## 打包 EXE

在项目目录双击 `build_exe.bat`，或在 PowerShell 执行：

```powershell
.\build_exe.bat
```

脚本会使用 `.venv` 中的 PyInstaller 生成 `dist_release/BilibiliNotifier/BilibiliNotifier.exe`，并使用项目根目录的 `app.ico` 作为图标。输出目录独立于已有的 `dist` 目录，避免清理打包文件时误碰运行中的 Cookie。分发时请保留整个 `BilibiliNotifier` 文件夹，不要只复制 EXE；登录 Cookie、动态历史和运行配置会保存在 EXE 同目录下。

## 使用流程

1. 等待登录完成，窗口顶部会显示当前 B 站账号昵称和 MID。
2. 登录成功后，二维码区域会自动切换为动态结果区域，并读取完整关注列表。
3. 在“动态”页面搜索昵称或 MID，并勾选需要监控的对象。
4. 点击“设置”页面，选择动态类型、检查间隔、关闭窗口行为和是否开机自启。
5. 点击“保存设置”。已有选中 UP 主时，登录完成后会自动开始监控。
6. 使用“立即检查”可以随时检查一次；它和定时检查使用相同的去重规则，只显示新动态。
7. 使用“暂停监控/开始监控”控制自动检查。暂停时仍可手动检查。

未选择任何 UP 主时不会监控任何对象。关注列表支持全选、清空和刷新；已选择的 MID 会保存，下次启动时自动恢复。

## 界面说明

- **动态页面**：使用 Windows 风格的 Qt 界面显示关注列表和新动态结果；“重新登录”会清除当前 Cookie 并重新扫码。
- **设置页面**：设置检查间隔、动态类型、关闭窗口行为、开机自启和声音提示。声音默认开启，每轮检查发现一条或多条新动态时播放一次；可选择四种 Windows 内置提示音，或选择本地 WAV 文件，并通过“测试声音”验证。WAV 文件失效时自动回退到默认系统提示音。
- **关注列表**：按昵称或 MID 搜索并多选 UP 主。
- **动态结果**：登录后替代二维码显示，每条动态展示 UP 主昵称、更新时间和内容，不显示 MID、类型或完整网址，动态之间以空行分隔。视频和转发视频显示可点击的 BV 号，单击后使用系统默认浏览器打开视频；图文和转发图文显示“查看动态”，单击打开对应动态页面。链接为蓝色下划线，鼠标移入显示手形。转发额外展示原作者和转发评论，无评论时显示“（无）”。结果区域只读，支持选中和复制；打开浏览器失败时，运行日志会提示可复制的网址。
- **运行日志**：只显示登录、关注列表、检查、错误等系统运行状态，不显示动态详情。
- **关闭窗口**：选择“隐藏到系统托盘”后点窗口关闭按钮，程序会继续监控并驻留在 Windows 系统托盘；选择“直接退出”则关闭程序。托盘菜单可恢复窗口或退出程序。底部“退出”按钮始终直接退出。
- **托盘通知**：发现新动态时按声音设置播放一次提示音；窗口隐藏时同时使托盘图标持续闪烁，恢复窗口后停止闪烁。声音提示与窗口是否隐藏无关。
- **开机自启**：设置页勾选后写入当前 Windows 用户的启动项，登录 Windows 后程序自动启动并隐藏到托盘。首次没有有效 Cookie 时会显示窗口用于扫码。

原始动态响应仍保存到 `bili/jsonAll.json`，已处理动态 ID 保存到 `bili/old_bvid.json`。

## 配置文件

界面保存的配置位于 `config.json`：

首次启动会自动生成默认配置，也可以将 `config.example.json` 复制为 `config.json` 后修改。本地配置、登录数据和打包产物已通过 `.gitignore` 排除，不会随源码提交。

```json
{
  "followed_dynamic_types": [
    "DYNAMIC_TYPE_AV",
    "DYNAMIC_TYPE_DRAW",
    "DYNAMIC_TYPE_FORWARD"
  ],
  "check_interval_minutes": 3,
  "followed_mids": ["123456"]
}
```

通常不需要手动编辑配置文件。`followed_mids` 是选中的关注 UP 主 MID，空列表代表当前未选择监控对象。

## 数据文件

- `bili/cookie.txt`：B 站登录 Cookie。
- `bili/jsonAll.json`：最近一次关注动态接口响应。
- `bili/old_bvid.json`：已处理动态 ID，用于去重。
- `www/wwwroot/qr.png`：当前登录二维码缓存。

系统托盘由 PySide6 的 `QSystemTrayIcon` 提供，安装 `requirements.txt` 后即可使用。无需打包为 EXE。

要重新登录，可以在界面点击“重新登录”，或删除 `bili/cookie.txt` 后重新启动。
