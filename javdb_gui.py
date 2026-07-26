"""Tkinter GUI for the JavDB NFO rating updater."""

from __future__ import annotations

import asyncio
import codecs
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

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


COLORS = {
    "bg": "#181b19",
    "surface": "#232724",
    "surface2": "#2d322e",
    "primary": "#2f8f67",
    "primary_hover": "#3da779",
    "success": "#55c58a",
    "error": "#ef6b6b",
    "warning": "#e7ad52",
    "text": "#edf1ee",
    "muted": "#a7b0aa",
    "border": "#414842",
}


def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        from ctypes import c_void_p, windll

        if windll.user32.SetProcessDpiAwarenessContext(c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


class JavDBApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("JavDB 番号评分刮削工具")
        self.root.configure(bg=COLORS["bg"])
        self.dpi = self._get_window_dpi()
        self.user_scale = self._get_user_scale()
        self.ui_scale = max(1.0, min(self.dpi / 96.0 * self.user_scale, 3.0))
        self.root.tk.call("tk", "scaling", self.dpi / 72.0 * self.user_scale)
        self._configure_window()

        self.folder_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="all")
        self.status_var = tk.StringVar(value="就绪")
        self.stats_var = tk.StringVar(value="更新文件: 0  |  失败番号: 0  |  总计: 0")
        self.progress_var = tk.DoubleVar(value=0)

        self.stop_event = threading.Event()
        self.ui_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.browser = None
        self.running = False
        self.close_pending = False
        self.all_results: list[dict] = []
        self.start_time: datetime | None = None

        self._configure_fonts()
        self._configure_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._drain_ui_queue)

    def _get_window_dpi(self) -> int:
        try:
            from ctypes import windll

            dpi = windll.user32.GetDpiForWindow(self.root.winfo_id())
            if dpi:
                return int(dpi)
        except Exception:
            pass
        try:
            return max(96, int(round(self.root.winfo_fpixels("1i"))))
        except tk.TclError:
            return 96

    def _px(self, value: int) -> int:
        return max(1, int(round(value * self.ui_scale)))

    @staticmethod
    def _get_user_scale() -> float:
        try:
            return max(0.8, min(float(os.environ.get("JAVDB_UI_SCALE", "1.0")), 2.0))
        except ValueError:
            return 1.0

    def _configure_window(self):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(self._px(1180), max(self._px(880), screen_width - self._px(64)))
        height = min(self._px(820), max(self._px(640), screen_height - self._px(96)))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(self._px(880), self._px(640))

    @staticmethod
    def _configure_fonts():
        font_specs = {
            "TkDefaultFont": ("Microsoft YaHei UI", 13, "normal"),
            "TkTextFont": ("Microsoft YaHei UI", 13, "normal"),
            "TkMenuFont": ("Microsoft YaHei UI", 12, "normal"),
            "TkHeadingFont": ("Microsoft YaHei UI", 13, "bold"),
            "TkFixedFont": ("Consolas", 12, "normal"),
        }
        for name, (family, size, weight) in font_specs.items():
            tkfont.nametofont(name).configure(family=family, size=size, weight=weight)

    def _configure_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 13))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Panel.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("Muted.Panel.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 26, "bold"))
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 13), foreground=COLORS["muted"])
        style.configure(
            "TButton",
            padding=(self._px(16), self._px(10)),
            background=COLORS["surface2"],
            foreground=COLORS["text"],
        )
        style.map("TButton", background=[("active", COLORS["border"]), ("disabled", COLORS["surface2"])])
        style.configure("Primary.TButton", background=COLORS["primary"], foreground="white")
        style.map("Primary.TButton", background=[("active", COLORS["primary_hover"]), ("disabled", COLORS["surface2"])])
        style.configure("Danger.TButton", background=COLORS["error"], foreground="white")
        style.configure("TRadiobutton", background=COLORS["surface"], foreground=COLORS["text"])
        style.map("TRadiobutton", background=[("active", COLORS["surface"])])
        style.configure(
            "Horizontal.TProgressbar",
            background=COLORS["primary"],
            troughcolor=COLORS["surface2"],
            borderwidth=0,
            thickness=self._px(12),
        )

    def _build_ui(self):
        container = ttk.Frame(self.root, padding=self._px(24))
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, self._px(18)))
        ttk.Label(header, text="JavDB 评分刮削", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="查询番号评分并安全更新 NFO", style="Subtitle.TLabel").pack(
            side=tk.LEFT, padx=(self._px(18), 0), pady=(self._px(6), 0)
        )

        settings = ttk.Frame(container, style="Panel.TFrame", padding=self._px(18))
        settings.pack(fill=tk.X, pady=(0, self._px(14)))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="目标文件夹", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, self._px(12))
        )
        self.folder_entry = tk.Entry(
            settings,
            textvariable=self.folder_var,
            bg=COLORS["surface2"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 13),
        )
        self.folder_entry.grid(row=0, column=1, sticky="ew", ipady=self._px(8))
        ttk.Button(settings, text="浏览...", command=self.browse_folder).grid(
            row=0, column=2, padx=(self._px(10), 0)
        )

        mode_row = ttk.Frame(settings, style="Panel.TFrame")
        mode_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(self._px(14), 0))
        ttk.Label(mode_row, text="模式", style="Panel.TLabel").pack(
            side=tk.LEFT, padx=(0, self._px(20))
        )
        ttk.Radiobutton(mode_row, text="全量更新", variable=self.mode_var, value="all").pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text="补全今日未成功或评分缺失的项目", variable=self.mode_var, value="fill").pack(
            side=tk.LEFT, padx=(self._px(18), 0)
        )

        actions = ttk.Frame(container)
        actions.pack(fill=tk.X, pady=(0, self._px(14)))
        self.start_btn = ttk.Button(actions, text="开始", style="Primary.TButton", command=self.start_scrape)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(actions, text="停止", style="Danger.TButton", command=self.stop_scrape, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(self._px(8), 0))
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=self._px(14))
        self.csv_btn = ttk.Button(actions, text="导出 CSV", command=lambda: self.export("csv"), state=tk.DISABLED)
        self.csv_btn.pack(side=tk.LEFT)
        self.json_btn = ttk.Button(actions, text="导出 JSON", command=lambda: self.export("json"), state=tk.DISABLED)
        self.json_btn.pack(side=tk.LEFT, padx=(self._px(8), 0))

        progress = ttk.Frame(container, style="Panel.TFrame", padding=self._px(18))
        progress.pack(fill=tk.X, pady=(0, self._px(14)))
        ttk.Label(progress, textvariable=self.status_var, style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Label(progress, textvariable=self.stats_var, style="Muted.Panel.TLabel").pack(side=tk.RIGHT)
        ttk.Progressbar(progress, variable=self.progress_var, maximum=100).pack(
            fill=tk.X, pady=(self._px(32), 0)
        )

        log_panel = ttk.Frame(container, style="Panel.TFrame", padding=self._px(14))
        log_panel.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_panel,
            state=tk.DISABLED,
            bg=COLORS["surface2"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary"],
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 12),
            padx=self._px(12),
            pady=self._px(12),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("success", foreground=COLORS["success"])
        self.log_text.tag_configure("error", foreground=COLORS["error"])
        self.log_text.tag_configure("warning", foreground=COLORS["warning"])
        self.log_text.tag_configure("info", foreground=COLORS["muted"])

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def log(self, message: str, tag: str | None = None):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n", tag or ())
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _post(self, action, *args):
        self.ui_queue.put((action, args))

    def _drain_ui_queue(self):
        try:
            while True:
                action, args = self.ui_queue.get_nowait()
                action(*args)
        except queue.Empty:
            pass
        try:
            self.root.after(50, self._drain_ui_queue)
        except tk.TclError:
            pass

    def _set_running(self, running: bool):
        self.running = running
        self.start_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)
        export_state = tk.NORMAL if self.all_results and not running else tk.DISABLED
        self.csv_btn.configure(state=export_state)
        self.json_btn.configure(state=export_state)

    def start_scrape(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择有效的文件夹路径")
            return

        self.stop_event.clear()
        self.all_results = []
        self.start_time = datetime.now()
        self.progress_var.set(0)
        self.stats_var.set("更新文件: 0  |  失败番号: 0  |  总计: 0")
        self.status_var.set("正在扫描 NFO...")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self._set_running(True)

        self.worker = threading.Thread(
            target=self._scrape_worker,
            args=(folder, self.mode_var.get()),
            name="javdb-worker",
            daemon=False,
        )
        self.worker.start()

    def stop_scrape(self):
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("正在安全停止...")
        self.log("停止请求已发送，当前操作结束后将保留进度", "warning")

    def _scrape_worker(self, folder: str, mode: str):
        summary = None
        try:
            settings = Settings.from_env()
            nfo_map, warnings = collect_nfo_map(folder, mode)
            for warning in warnings:
                self._post(self.log, warning, "warning")
            if not nfo_map:
                self._post(self.log, "未找到符合条件且包含有效番号的 NFO 文件", "warning")
                self._post(self._finish, "completed", None)
                return

            codes, resumed = pending_codes(folder, nfo_map)
            file_count = sum(len(paths) for paths in nfo_map.values())
            self._post(self.log, f"找到 {file_count} 个 NFO 文件，{len(nfo_map)} 个唯一番号", "info")
            if resumed:
                self._post(self.log, f"断点续传：跳过 {resumed} 个已成功番号", "info")
            if not codes:
                self._post(self.log, "没有待查询番号", "info")
                clear_progress(folder)
                self._post(self._finish, "completed", None)
                return

            async def run():
                self._post(self.status_var.set, "正在启动浏览器...")
                self.browser, active_proxy = await start_browser(settings)
                self._post(self.log, f"浏览器已启动，连接方式：{active_proxy or '直连'}", "info")

                def on_log(message, tag):
                    self._post(self.log, message, tag)

                def on_progress(current, code):
                    completed = current.processed
                    percent = completed / current.total * 100 if current.total else 100
                    position = min(completed + 1, current.total)
                    self._post(self.progress_var.set, percent)
                    self._post(self.status_var.set, f"查询中 ({position}/{current.total}): {code}")
                    self._post(
                        self.stats_var.set,
                        f"更新文件: {current.updated_files}  |  失败番号: {current.failed}  |  总计: {current.total}",
                    )

                return await process_codes(
                    self.browser,
                    codes,
                    nfo_map,
                    folder,
                    settings,
                    self.stop_event.is_set,
                    on_log,
                    on_progress,
                )

            summary = asyncio.run(run())
            self.all_results = summary.results
            self._post(self._finish, summary.status, summary)
        except CoreError as exc:
            self._post(self.log, str(exc), "error")
            self._post(self._finish, "failed", summary)
        except Exception as exc:
            self._post(self.log, f"未预期错误：{exc}", "error")
            self._post(self._finish, "failed", summary)
        finally:
            stop_browser(self.browser)
            self.browser = None

    def _finish(self, status: str, summary):
        labels = {
            "completed": "完成",
            "cancelled": "已停止，进度已保留",
            "needs_verification": "需要完成浏览器验证，进度已保留",
            "failed": "失败，进度已保留",
        }
        self.status_var.set(labels.get(status, status))
        if status == "completed":
            self.progress_var.set(100)
        if summary is not None:
            self.stats_var.set(
                f"更新文件: {summary.updated_files}  |  失败番号: {summary.failed}  |  总计: {summary.total}"
            )
            tag = "success" if status == "completed" and summary.failed == 0 else "warning"
            self.log(f"任务结束：{labels.get(status, status)}", tag)
        self._set_running(False)
        if self.close_pending:
            self.root.destroy()

    def export(self, fmt: str):
        if not self.all_results:
            messagebox.showinfo("提示", "没有可导出的查询结果")
            return
        try:
            path = export_results(
                self.all_results,
                self.folder_var.get().strip(),
                fmt,
                self.start_time or datetime.now(),
            )
            messagebox.showinfo("导出成功", f"结果已导出到：\n{path}")
        except (CoreError, OSError) as exc:
            messagebox.showerror("导出失败", str(exc))

    def on_close(self):
        if not self.running:
            self.root.destroy()
            return
        if not messagebox.askyesno("退出", "任务仍在运行。是否安全停止并退出？"):
            return
        self.close_pending = True
        self.stop_scrape()


def setup_console():
    if sys.platform != "win32":
        return
    try:
        if sys.stdout and hasattr(sys.stdout, "buffer"):
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        if sys.stderr and hasattr(sys.stderr, "buffer"):
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
    except Exception:
        pass


def main():
    setup_console()
    enable_dpi_awareness()
    root = tk.Tk()
    JavDBApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
