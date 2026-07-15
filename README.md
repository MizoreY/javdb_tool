# JavDB 番号评分刮削工具

> 自动从 JavDB 查询番号评分，批量更新 Emby / Jellyfin 媒体库 NFO 文件中的评分字段，提供 CLI 与 GUI 两种使用方式。

## ✨ 功能概述

- **全量扫描**：扫描指定文件夹内所有 `.nfo`，提取番号并更新评分。
- **补全模式**：只处理今日未更新或评分缺失的番号，适合日常定期维护。
- **Cloudflare 重试**：触发人机验证时自动等待重试，连续 3 次才判定封禁。
- **断点续传**：进度保存在目标文件夹的 `.javdb_progress.json`，中断后可继续。
- **结果导出**：支持 CSV（UTF-8 BOM，Excel 可直接打开）/ JSON 导出，含番号、评分、标题、链接、状态、文件路径。
- **CLI + GUI**：命令行脚本与 Tkinter 图形界面任选。

## 📊 评分规则

- `rating` = 评分四舍五入保留一位小数
- `criticrating` = 评分 × 20 保留一位小数

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.9+ |
| 浏览器自动化 | nodriver（无头 Chrome，绕过 Cloudflare）|
| HTML 解析 | BeautifulSoup4 |
| GUI | Tkinter |
| 数据格式 | NFO（XML）· CSV · JSON |
| 依赖环境 | Chrome 浏览器 + 代理 |

## 🚀 使用方法

### 环境要求
- Python 3.9+
- Chrome 浏览器
- 可访问 JavDB 的代理

### 安装依赖
```bash
pip install nodriver beautifulsoup4
```

### CLI 版
1. 双击 `启动.bat`
2. 选择模式（1 全量 / 2 补全）
3. 输入视频文件夹路径
4. 确认开始（y）→ 等待查询 → 选择是否导出

### GUI 版
1. 双击 `启动GUI.bat`
2. 点击「浏览」选择文件夹
3. 选择模式 → 点击「开始刮削」

### 环境变量（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `JAVDB_PROXY` | 代理地址 | `http://127.0.0.1:7890` |
| `JAVDB_DELAY_MIN` | 最小间隔秒数 | `8` |
| `JAVDB_DELAY_MAX` | 最大间隔秒数 | `15` |

## 📁 项目结构

```
javdb_tool/
├── javdb_rating.py     # CLI 主程序：扫描 NFO、查询评分、更新与导出
├── javdb_gui.py        # Tkinter 图形界面
├── 启动.bat            # CLI 启动脚本
├── 启动GUI.bat         # GUI 启动脚本
├── 说明.txt            # 详细中文说明
└── README.md
```

## ⚠️ 注意事项

- 修改 NFO 后需在 Emby / Jellyfin 中刷新媒体库。
- 连续 3 次触发 Cloudflare 验证会暂停，需手动在浏览器验证后继续。
- 请合理设置请求间隔，避免对目标站点造成压力；仅供个人媒体库管理使用。
