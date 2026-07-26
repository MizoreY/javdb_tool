# JavDB 番号评分刮削工具

从 JavDB 查询番号评分，批量、安全地更新 Emby / Jellyfin 使用的 NFO 文件。提供 GUI 和 CLI，两者共享同一套核心逻辑。

## 一键启动 GUI

双击 **`启动GUI.bat`**。

启动器会自动完成以下工作：

1. 查找 Python 3.11 或更高版本；过旧或损坏的运行时不会继续使用。
2. 如果没有合适的 Python，优先使用 Windows `winget`；没有 `winget` 时从 Python 官网下载经过数字签名校验的 64 位安装器，并安装到项目自己的 `.python` 目录，不修改系统 PATH。
3. 在项目目录创建隔离的 `.venv` 虚拟环境。
4. 首次运行时自动安装锁定版本的依赖。
5. 打开 GUI。后续启动会直接复用环境。

首次启动需要网络，可能需要几分钟。启动失败时查看项目目录中的 `launcher.log`。

## 使用方式

1. 点击“浏览...”并选择媒体库文件夹。
2. 选择模式：
   - **全量更新**：查询所有包含有效 `<num>` 的 NFO。
   - **补全模式**：处理今天尚未成功更新或评分缺失/无效的项目。
3. 点击“开始”。
4. 完成后可将本次查询结果导出为 CSV 或 JSON，文件保存在目标媒体文件夹。

默认使用代理 `http://127.0.0.1:7890`。通过代理启动浏览器失败时会自动尝试直连；也可以把 `JAVDB_PROXY` 设置为空以直接连接。

## 数据安全

- 优先使用搜索结果中包含目标番号的项目；没有包含匹配时使用第一条搜索结果。
- 评分必须在 `0–5` 范围内。
- NFO 先写临时文件，再原子替换原文件。
- 每次修改前保留同目录 `原文件名.nfo.bak` 备份。
- 成功进度保存在目标目录的 `.javdb_progress.json`；停止、异常和部分失败时不会清除。
- 每日成功状态保存在 `.javdb_state.json`，补全模式不依赖易变化的文件修改时间。
- XML 或状态文件损坏时会显示具体文件和错误，不会静默覆盖。

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `JAVDB_PROXY` | 代理地址；设置为空表示直连 | `http://127.0.0.1:7890` |
| `JAVDB_DELAY_MIN` | 请求之间的最短等待秒数 | `8` |
| `JAVDB_DELAY_MAX` | 请求之间的最长等待秒数 | `15` |
| `JAVDB_PYPI_INDEX` | 首次安装依赖使用的 PyPI 镜像 | `https://pypi.org/simple` |
| `JAVDB_UI_SCALE` | GUI 额外缩放倍率，可用于高分屏微调 | `1.0` |

间隔必须满足 `0 <= JAVDB_DELAY_MIN <= JAVDB_DELAY_MAX`。

## CLI

双击 `启动.bat`。它与 GUI 使用同一个自动安装启动器和虚拟环境。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

核心模块划分：

```text
javdb_core.py    搜索、结果选择、NFO、状态、批处理与导出
javdb_gui.py     Tkinter GUI 和线程协调
javdb_rating.py  CLI
launcher.ps1     Python/虚拟环境/依赖引导
requirements.lock 完整锁定的运行依赖
tests/           离线单元测试
```

## 注意事项

- 需要安装 Chrome。
- 请合理设置请求间隔，仅用于个人媒体库管理。
- 遇到 Cloudflare 验证时，任务会停止并保留成功进度；验证后重新点击“开始”即可续传。
- 更新 NFO 后需要在 Emby / Jellyfin 中刷新媒体库。
