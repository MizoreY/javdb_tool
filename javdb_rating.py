"""Command-line interface for the JavDB NFO rating updater."""

from __future__ import annotations

import asyncio
import codecs
import os
import sys
import threading
from datetime import datetime

from javdb_core import (
    CoreError,
    Settings,
    clear_progress,
    collect_nfo_map,
    export_results,
    pending_codes,
    process_codes,
    start_browser,
    stop_browser,
)


def setup_console():
    if sys.platform != "win32":
        return
    os.system("chcp 65001 >NUL 2>&1")
    try:
        if sys.stdout and hasattr(sys.stdout, "buffer"):
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        if sys.stderr and hasattr(sys.stderr, "buffer"):
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
    except Exception:
        pass


async def run_job(folder: str, mode: str, stop_event: threading.Event):
    settings = Settings.from_env()
    nfo_map, warnings = collect_nfo_map(folder, mode)
    for warning in warnings:
        print(f"[跳过] {warning}")
    if not nfo_map:
        print("未找到符合条件且包含有效番号的 NFO 文件。")
        return None

    codes, resumed = pending_codes(folder, nfo_map)
    print(f"找到 {sum(map(len, nfo_map.values()))} 个 NFO 文件，{len(nfo_map)} 个唯一番号。")
    if resumed:
        print(f"断点续传：跳过 {resumed} 个已成功番号。")
    if not codes:
        print("没有待查询番号。")
        clear_progress(folder)
        return None

    browser = None
    try:
        print("正在启动浏览器...")
        browser, active_proxy = await start_browser(settings)
        print(f"浏览器已启动，连接方式：{active_proxy or '直连'}")

        def on_log(message, tag):
            labels = {"error": "失败", "warning": "警告", "success": "更新", "info": "信息"}
            print(f"[{labels.get(tag, tag)}] {message}")

        def on_progress(summary, code):
            position = min(summary.processed + 1, summary.total)
            print(f"[{position}/{summary.total}] {code}")

        return await process_codes(
            browser,
            codes,
            nfo_map,
            folder,
            settings,
            stop_event.is_set,
            on_log,
            on_progress,
        )
    finally:
        stop_browser(browser)


def choose_mode() -> str:
    print("1 - 全量更新")
    print("2 - 补全今日未成功或评分缺失的项目")
    return "fill" if input("请选择模式 (1/2): ").strip() == "2" else "all"


def main():
    setup_console()
    print("=" * 60)
    print("JavDB 番号评分刮削工具")
    print("=" * 60)
    mode = choose_mode()
    folder = input("请输入目标文件夹: ").strip().strip('"')
    if not os.path.isdir(folder):
        raise CoreError(f"文件夹不存在: {folder}")
    if input("确认开始？(y/n): ").strip().lower() != "y":
        print("已取消。")
        return

    stop_event = threading.Event()
    start_time = datetime.now()
    try:
        summary = asyncio.run(run_job(folder, mode, stop_event))
    except KeyboardInterrupt:
        stop_event.set()
        print("\n已中断，成功进度已保留。")
        return
    if summary is None:
        return

    status_labels = {
        "completed": "完成",
        "partial": "部分完成，失败项目将在下次重试",
        "cancelled": "已停止，进度已保留",
        "needs_verification": "需要完成 Cloudflare 验证",
    }
    print(f"\n状态：{status_labels.get(summary.status, summary.status)}")
    print(f"更新文件：{summary.updated_files}，失败番号：{summary.failed}")

    if summary.results:
        choice = input("导出结果？c=CSV, j=JSON, b=两者, n=不导出: ").strip().lower()
        if choice in {"c", "b"}:
            print(f"CSV: {export_results(summary.results, folder, 'csv', start_time)}")
        if choice in {"j", "b"}:
            print(f"JSON: {export_results(summary.results, folder, 'json', start_time)}")


if __name__ == "__main__":
    try:
        main()
    except CoreError as exc:
        print(f"错误：{exc}")
    except Exception as exc:
        print(f"未预期错误：{exc}")
    finally:
        try:
            input("\n按回车键退出...")
        except EOFError:
            pass
