import asyncio
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import sys
import codecs
import warnings
import random
import json
import re
import csv
import xml.etree.ElementTree as ET
from datetime import datetime
import nodriver as uc
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")

BASE_URL = "https://javdb.com"
SEARCH_URL = f"{BASE_URL}/search"
PROGRESS_FILE = ".javdb_progress.json"
PROXY = os.environ.get("JAVDB_PROXY", "http://127.0.0.1:7890")
DELAY_MIN = float(os.environ.get("JAVDB_DELAY_MIN", "8"))
DELAY_MAX = float(os.environ.get("JAVDB_DELAY_MAX", "15"))

COLORS = {
    "bg": "#1a1b2e",
    "surface": "#232440",
    "surface2": "#2a2b4a",
    "primary": "#7c5cfc",
    "primary_hover": "#9b82fd",
    "success": "#4ade80",
    "error": "#f87171",
    "warning": "#fbbf24",
    "text": "#e2e8f0",
    "text_dim": "#94a3b8",
    "border": "#3b3c5e",
}


def parse_score_value(rating_str):
    if not rating_str:
        return None
    try:
        m = re.search(r'[\d.]+', rating_str)
        if m:
            return float(m.group())
    except (ValueError, IndexError):
        pass
    return None


async def search_code(browser, code: str) -> dict:
    result = {"code": code, "rating": None, "title": None, "url": None, "error": None}
    try:
        url = f"{SEARCH_URL}?q={code}&f=all"
        page = await browser.get(url)
        await page.sleep(4)

        html = await page.get_content()
        soup = BeautifulSoup(html, "html.parser")

        if "Just a moment" in html or "challenge" in html.lower():
            result["error"] = "触发Cloudflare验证，可能被封禁"
            return result

        items = soup.select(".movie-list .item")
        if not items:
            result["error"] = "未找到结果"
            return result

        target = None
        for item in items:
            uid_el = item.select_one(".uid")
            if uid_el and code.upper() in uid_el.text.strip().upper():
                target = item
                break
        if not target:
            target = items[0]

        link_el = target.select_one("a")
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                result["url"] = BASE_URL + href
            else:
                result["url"] = href

        title_el = target.select_one(".video-title")
        if title_el:
            result["title"] = title_el.text.strip()

        score_el = target.select_one(".score .value")
        if score_el:
            result["rating"] = score_el.text.strip()
        else:
            result["rating"] = "无评分"

    except Exception as e:
        result["error"] = str(e)
    return result


def find_nfo_files(folder):
    nfo_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".nfo"):
                nfo_files.append(os.path.join(root, f))
    return nfo_files


def load_progress(folder):
    path = os.path.join(folder, PROGRESS_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"done": []}


def save_progress(folder, progress):
    path = os.path.join(folder, PROGRESS_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def clear_progress(folder):
    path = os.path.join(folder, PROGRESS_FILE)
    if os.path.exists(path):
        os.remove(path)


def extract_code_from_nfo(nfo_path):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        num_el = root.find("num")
        if num_el is not None and num_el.text:
            return num_el.text.strip().upper()
    except Exception:
        pass
    return None


def check_criticrating(nfo_path):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        critic_el = root.find("criticrating")
        if critic_el is None or not critic_el.text:
            return True
        val = float(critic_el.text.strip())
        return val < 10
    except Exception:
        return True


def update_nfo_rating(nfo_path, score, rating_val, critic_rating):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        rating_el = root.find("rating")
        old_rating = rating_el.text.strip() if rating_el is not None and rating_el.text else ""
        if rating_el is None:
            rating_el = ET.SubElement(root, "rating")
        rating_el.text = "%.1f" % rating_val

        critic_el = root.find("criticrating")
        if critic_el is None:
            critic_el = ET.SubElement(root, "criticrating")
        critic_el.text = "%.1f" % critic_rating

        tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
        return True, old_rating
    except Exception as e:
        return str(e), None


def export_results_csv(results, start_time):
    filename = start_time.strftime("javdb_results_%Y%m%d_%H%M%S.csv")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(script_dir, filename)

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["番号", "评分", "标题", "链接", "状态", "文件路径"])
        for res in results:
            code = res.get("code", "")
            rating = res.get("rating", "")
            title = res.get("title", "")
            url = res.get("url", "")
            error = res.get("error", "")
            status = "成功" if not error else error
            files = res.get("files", [])
            file_paths = "; ".join(files) if files else ""
            writer.writerow([code, rating, title, url, status, file_paths])

    return filepath


def export_results_json(results, folder, start_time):
    filename = start_time.strftime("javdb_results_%Y%m%d_%H%M%S.json")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(script_dir, filename)

    export_data = {
        "folder": folder,
        "export_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "results": []
    }

    for res in results:
        export_data["results"].append({
            "code": res.get("code", ""),
            "rating": res.get("rating", ""),
            "title": res.get("title", ""),
            "url": res.get("url", ""),
            "error": res.get("error", ""),
            "files": res.get("files", [])
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return filepath


class ModernButton(tk.Canvas):
    def __init__(self, parent, text, command=None, bg=COLORS["primary"], hover=COLORS["primary_hover"],
                 fg="white", width=120, height=36, radius=8, **kwargs):
        super().__init__(parent, width=width, height=height, bg=COLORS["bg"],
                         highlightthickness=0, cursor="hand2", **kwargs)
        self.command = command
        self.bg_color = bg
        self.hover_color = hover
        self.fg_color = fg
        self.radius = radius
        self.w = width
        self.h = height
        self._text = text
        self._enabled = True

        self._draw(bg)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_click)

    def _draw(self, color):
        self.delete("all")
        r = self.radius
        w, h = self.w, self.h
        self.create_arc(0, 0, r*2, r*2, start=90, extent=90, fill=color, outline="")
        self.create_arc(w-r*2, 0, w, r*2, start=0, extent=90, fill=color, outline="")
        self.create_arc(0, h-r*2, r*2, h, start=180, extent=90, fill=color, outline="")
        self.create_arc(w-r*2, h-r*2, w, h, start=270, extent=90, fill=color, outline="")
        self.create_rectangle(r, 0, w-r, h, fill=color, outline="")
        self.create_rectangle(0, r, w, h-r, fill=color, outline="")
        self.create_text(w/2, h/2, text=self._text, fill=self.fg_color,
                         font=("Microsoft YaHei UI", 10, "bold"))

    def _on_enter(self, e):
        if self._enabled:
            self._draw(self.hover_color)

    def _on_leave(self, e):
        if self._enabled:
            self._draw(self.bg_color)

    def _on_click(self, e):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        if enabled:
            self._draw(self.bg_color)
            self.config(cursor="hand2")
        else:
            self._draw(COLORS["surface2"])
            self.config(cursor="arrow")


class ModernRadioButton(tk.Frame):
    def __init__(self, parent, text, variable, value, **kwargs):
        super().__init__(parent, bg=COLORS["surface"], **kwargs)
        self.variable = variable
        self.value = value
        self._text = text

        self.canvas = tk.Canvas(self, width=20, height=20, bg=COLORS["surface"],
                                highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=(0, 10))

        self.label = tk.Label(self, text=text, font=("Microsoft YaHei UI", 12),
                              bg=COLORS["surface"], fg=COLORS["text"], cursor="hand2")
        self.label.pack(side=tk.LEFT)

        self._draw()
        self.variable.trace_add("write", lambda *_: self._draw())

        for widget in [self, self.canvas, self.label]:
            widget.bind("<Button-1>", self._select)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _draw(self):
        self.canvas.delete("all")
        x, y, r = 10, 10, 7
        if self.variable.get() == self.value:
            self.canvas.create_oval(x-r, y-r, x+r, y+r, outline=COLORS["primary"], width=2)
            self.canvas.create_oval(x-r+3, y-r+3, x+r-3, y+r-3, fill=COLORS["primary"], outline="")
        else:
            self.canvas.create_oval(x-r, y-r, x+r, y+r, outline=COLORS["border"], width=2)

    def _select(self, e=None):
        self.variable.set(self.value)

    def _on_enter(self, e):
        self.label.configure(fg=COLORS["primary_hover"])

    def _on_leave(self, e):
        self.label.configure(fg=COLORS["text"])


class JavDBApp:
    def __init__(self, root):
        self.root = root
        self.root.title("JavDB 番号评分刮削工具")
        self.root.geometry("1280x800")
        self.root.minsize(860, 600)
        self.root.configure(bg=COLORS["bg"])

        self.folder_var = tk.StringVar()
        self.mode_var = tk.IntVar(value=1)
        self.running = False
        self.stop_flag = False
        self.browser = None
        self.nfo_map = {}
        self.all_results = []
        self.start_time = None

        self._set_dpi_aware()
        self._configure_styles()
        self.setup_ui()

    def _set_dpi_aware(self):
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    def _configure_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                         font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 28, "bold"),
                         foreground=COLORS["text"], background=COLORS["bg"])
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 12),
                         foreground=COLORS["text_dim"], background=COLORS["bg"])
        style.configure("Stats.TLabel", font=("Microsoft YaHei UI", 10),
                         foreground=COLORS["text_dim"], background=COLORS["bg"])
        style.configure("TLabelframe", background=COLORS["surface"],
                         foreground=COLORS["text"], borderwidth=0)
        style.configure("TLabelframe.Label", background=COLORS["surface"],
                         foreground=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"))

        style.configure("Horizontal.TProgressbar",
                         background=COLORS["primary"],
                         troughcolor=COLORS["surface2"],
                         borderwidth=0,
                         lightcolor=COLORS["primary"],
                         darkcolor=COLORS["primary"])

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=32)
        main_frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main_frame)
        header.pack(fill=tk.X, pady=(0, 32))
        ttk.Label(header, text="JavDB 评分刮削", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="自动从 JavDB 查询评分并更新 NFO 文件",
                  style="Subtitle.TLabel").pack(side=tk.LEFT, padx=(24, 0), pady=(6, 0))

        content = ttk.Frame(main_frame)
        content.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(content, width=500)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 32))
        left_panel.pack_propagate(False)

        folder_frame = tk.Frame(left_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                                highlightthickness=1, bd=0)
        folder_frame.pack(fill=tk.X, pady=(0, 12))

        folder_inner = tk.Frame(folder_frame, bg=COLORS["surface"])
        folder_inner.pack(padx=20, pady=20)

        tk.Label(folder_inner, text="目标文件夹", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(anchor=tk.W)

        folder_entry_frame = tk.Frame(folder_inner, bg=COLORS["surface2"],
                                       highlightbackground=COLORS["border"],
                                       highlightthickness=1, bd=0)
        folder_entry_frame.pack(fill=tk.X, pady=(8, 0))

        self.folder_entry = tk.Entry(folder_entry_frame, textvariable=self.folder_var,
                                     font=("Microsoft YaHei UI", 11), bg=COLORS["surface2"],
                                     fg=COLORS["text"], insertbackground=COLORS["text"],
                                     relief=tk.FLAT, bd=10)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        browse_btn = tk.Label(folder_entry_frame, text="浏览", font=("Microsoft YaHei UI", 11),
                              bg=COLORS["primary"], fg="white", padx=16, pady=8, cursor="hand2")
        browse_btn.pack(side=tk.RIGHT, padx=(4, 0))
        browse_btn.bind("<Button-1>", lambda e: self.browse_folder())
        browse_btn.bind("<Enter>", lambda e: browse_btn.configure(bg=COLORS["primary_hover"]))
        browse_btn.bind("<Leave>", lambda e: browse_btn.configure(bg=COLORS["primary"]))

        mode_frame = tk.Frame(left_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                              highlightthickness=1, bd=0)
        mode_frame.pack(fill=tk.X, pady=(0, 12))

        mode_inner = tk.Frame(mode_frame, bg=COLORS["surface"])
        mode_inner.pack(padx=20, pady=20)

        tk.Label(mode_inner, text="刮削模式", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(anchor=tk.W)

        ModernRadioButton(mode_inner, "全量扫描（扫描所有番号）",
                          self.mode_var, 1).pack(anchor=tk.W, pady=(12, 4))
        ModernRadioButton(mode_inner, "补全模式（今日未更新或评分缺失）",
                          self.mode_var, 2).pack(anchor=tk.W, pady=4)

        btn_frame = tk.Frame(left_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                             highlightthickness=1, bd=0)
        btn_frame.pack(fill=tk.X, pady=(0, 12))

        btn_inner = tk.Frame(btn_frame, bg=COLORS["surface"])
        btn_inner.pack(padx=20, pady=20)

        self.start_btn = ModernButton(btn_inner, "开始刮削", command=self.start_scrape,
                                      bg=COLORS["primary"], hover=COLORS["primary_hover"],
                                      width=450, height=56)
        self.start_btn.pack(fill=tk.X)

        self.stop_btn = ModernButton(btn_inner, "停止", command=self.stop_scrape,
                                     bg=COLORS["error"], hover="#ef5350",
                                     width=450, height=56, fg="white")
        self.stop_btn.pack(fill=tk.X, pady=(8, 0))
        self.stop_btn.set_enabled(False)

        export_frame = tk.Frame(left_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                                highlightthickness=1, bd=0)
        export_frame.pack(fill=tk.X)

        export_inner = tk.Frame(export_frame, bg=COLORS["surface"])
        export_inner.pack(padx=20, pady=20)

        tk.Label(export_inner, text="导出结果", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(anchor=tk.W)

        export_btns = tk.Frame(export_inner, bg=COLORS["surface"])
        export_btns.pack(fill=tk.X, pady=(8, 0))

        ModernButton(export_btns, "CSV", command=lambda: self.export("csv"),
                     bg=COLORS["surface2"], hover=COLORS["border"],
                     width=210, height=40, fg=COLORS["text"]).pack(side=tk.LEFT)
        ModernButton(export_btns, "JSON", command=lambda: self.export("json"),
                     bg=COLORS["surface2"], hover=COLORS["border"],
                     width=210, height=40, fg=COLORS["text"]).pack(side=tk.RIGHT)

        right_panel = ttk.Frame(content)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        progress_frame = tk.Frame(right_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                                  highlightthickness=1, bd=0)
        progress_frame.pack(fill=tk.X, pady=(0, 16))

        progress_inner = tk.Frame(progress_frame, bg=COLORS["surface"])
        progress_inner.pack(padx=20, pady=20)

        progress_header = tk.Frame(progress_inner, bg=COLORS["surface"])
        progress_header.pack(fill=tk.X)

        tk.Label(progress_header, text="进度", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(progress_header, textvariable=self.status_var,
                 font=("Microsoft YaHei UI", 11), bg=COLORS["surface"],
                 fg=COLORS["text_dim"]).pack(side=tk.RIGHT)

        self.progress_var = tk.DoubleVar()
        style = ttk.Style()
        style.configure("Custom.Horizontal.TProgressbar", background=COLORS["primary"],
                        troughcolor=COLORS["surface2"], borderwidth=0, lightcolor=COLORS["primary"],
                        darkcolor=COLORS["primary"])
        self.progress_bar = ttk.Progressbar(progress_inner, variable=self.progress_var,
                                            maximum=100, style="Custom.Horizontal.TProgressbar",
                                            length=300)
        self.progress_bar.pack(fill=tk.X, pady=(12, 0))

        stats_frame = tk.Frame(progress_inner, bg=COLORS["surface"])
        stats_frame.pack(fill=tk.X, pady=(12, 0))

        self.stats_var = tk.StringVar(value="更新: 0  |  失败: 0  |  总计: 0")
        tk.Label(stats_frame, textvariable=self.stats_var,
                 font=("Consolas", 12), bg=COLORS["surface"],
                 fg=COLORS["text_dim"]).pack(anchor=tk.W)

        log_frame = tk.Frame(right_panel, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                             highlightthickness=1, bd=0)
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_header = tk.Frame(log_frame, bg=COLORS["surface"])
        log_header.pack(fill=tk.X, padx=20, pady=(20, 0))

        tk.Label(log_header, text="运行日志", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(side=tk.LEFT)

        log_container = tk.Frame(log_frame, bg=COLORS["surface"])
        log_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 20))

        self.log_text = scrolledtext.ScrolledText(
            log_container, height=8, state=tk.DISABLED,
            font=("Consolas", 11), bg=COLORS["surface2"], fg=COLORS["text"],
            insertbackground=COLORS["text"], selectbackground=COLORS["primary"],
            relief=tk.FLAT, bd=8, wrap=tk.WORD,
            highlightbackground=COLORS["border"], highlightthickness=1
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("success", foreground=COLORS["success"])
        self.log_text.tag_configure("error", foreground=COLORS["error"])
        self.log_text.tag_configure("warning", foreground=COLORS["warning"])
        self.log_text.tag_configure("info", foreground=COLORS["text_dim"])

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def log(self, msg, tag=None):
        self.log_text.config(state=tk.NORMAL)
        if tag:
            self.log_text.insert(tk.END, msg + "\n", tag)
        else:
            self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_status(self, msg):
        self.status_var.set(msg)

    def update_stats(self, updated, failed, total):
        self.stats_var.set(f"更新: {updated}  |  失败: {failed}  |  总计: {total}")

    def start_scrape(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择有效的文件夹路径")
            return

        self.running = True
        self.stop_flag = False
        self.all_results = []
        self.start_time = datetime.now()

        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)

        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        thread = threading.Thread(target=self.scrape_thread, args=(folder,), daemon=True)
        thread.start()

    def stop_scrape(self):
        self.stop_flag = True
        self.update_status("正在停止...")
        self.log("用户请求停止，等待当前查询完成...", "warning")

    def scrape_thread(self, folder):
        mode = self.mode_var.get()

        if mode == 1:
            nfo_files = find_nfo_files(folder)
        else:
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            nfo_files = []
            for f in find_nfo_files(folder):
                mtime = os.path.getmtime(f)
                is_stale = mtime < today_start
                is_low_rating = check_criticrating(f)
                if is_stale or is_low_rating:
                    nfo_files.append(f)

        if not nfo_files:
            self.root.after(0, lambda: self.log("未找到符合条件的 .nfo 文件", "warning"))
            self.root.after(0, self.finish_scrape)
            return

        self.nfo_map = {}
        for nfo_path in nfo_files:
            code = extract_code_from_nfo(nfo_path)
            if code:
                if code not in self.nfo_map:
                    self.nfo_map[code] = []
                self.nfo_map[code].append(nfo_path)

        unique_codes = list(self.nfo_map.keys())
        total = len(unique_codes)

        self.root.after(0, lambda: self.log(f"找到 {len(nfo_files)} 个 NFO 文件，{total} 个唯一番号"))
        self.root.after(0, lambda: self.update_status(f"准备查询 {total} 个番号..."))

        progress = load_progress(folder)
        done_codes = set(progress.get("done", []))
        query_codes = [c for c in unique_codes if c not in done_codes]

        if not query_codes:
            self.root.after(0, lambda: self.log("所有番号均已完成，无需查询", "info"))
            self.root.after(0, self.finish_scrape)
            return

        self.root.after(0, lambda: self.log(f"待查询: {len(query_codes)} 个"))

        stats = {"updated": 0, "failed": 0}
        current = [0]

        async def run():
            self.browser = await uc.start(
                headless=False,
                browser_args=[
                    f"--proxy-server={PROXY}",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            consecutive_cf = 0
            i = 0
            while i < len(query_codes):
                if self.stop_flag:
                    self.root.after(0, lambda: self.log("已停止", "warning"))
                    break

                code = query_codes[i]
                current[0] = i + 1
                self.root.after(0, lambda c=code, idx=i: self.update_status(f"查询中 ({idx+1}/{len(query_codes)}): {c}"))
                self.root.after(0, lambda idx=i, total=len(query_codes): self.progress_var.set(idx / total * 100))

                res = await search_code(self.browser, code)

                if "Cloudflare" in (res["error"] or ""):
                    consecutive_cf += 1
                    if consecutive_cf >= 3:
                        self.root.after(0, lambda: self.log(f"  连续{consecutive_cf}次触发Cloudflare验证，可能已被封禁", "error"))
                        self.root.after(0, lambda: self.log("  已停止，请在浏览器中手动验证后重试", "error"))
                        self.stop_flag = True
                        break
                    else:
                        self.root.after(0, lambda c=code, cf=consecutive_cf: self.log(f"  {c} 触发Cloudflare验证，10秒后重试... ({cf}/3)", "warning"))
                        await asyncio.sleep(10)
                        continue

                consecutive_cf = 0
                self.all_results.append({**res, "files": [os.path.basename(p) for p in self.nfo_map.get(code, [])]})

                if res["error"]:
                    self.root.after(0, lambda r=res: self.log(f"  {r['code']} - 错误: {r['error']}", "error"))
                    stats["failed"] += 1
                else:
                    score = parse_score_value(res["rating"])
                    if score is not None:
                        rating_val = round(score, 1)
                        critic_rating = round(score * 20, 1)
                        for nfo_path in self.nfo_map.get(code, []):
                            result, old_rating = update_nfo_rating(nfo_path, score, rating_val, critic_rating)
                            if result is True:
                                self.root.after(0, lambda c=code, rv=rating_val, cr=critic_rating, f=os.path.basename(nfo_path):
                                    self.log(f"  [已更新] {c} -> rating={rv:.1f}, criticrating={cr:.1f}  {f}", "success"))
                                stats["updated"] += 1
                            else:
                                self.root.after(0, lambda c=code, r=result: self.log(f"  [失败] {c} - {r}", "error"))
                                stats["failed"] += 1
                    else:
                        self.root.after(0, lambda r=res: self.log(f"  {r['code']} - 无评分: {r['rating']}", "warning"))

                if code not in progress.get("done", []):
                    progress.setdefault("done", []).append(code)
                    save_progress(folder, progress)

                self.root.after(0, lambda u=stats["updated"], f=stats["failed"], t=len(query_codes):
                    self.update_stats(u, f, t))

                if i < len(query_codes) - 1:
                    delay = random.uniform(DELAY_MIN, DELAY_MAX)
                    await asyncio.sleep(delay)

                i += 1

            self.browser.stop()

        try:
            asyncio.run(run())
        except Exception as e:
            self.root.after(0, lambda: self.log(f"查询出错: {e}", "error"))

        clear_progress(folder)

        self.root.after(0, lambda: self.progress_var.set(100))
        self.root.after(0, lambda: self.update_stats(stats["updated"], stats["failed"], len(query_codes)))
        self.root.after(0, self.finish_scrape)

    def finish_scrape(self):
        self.running = False
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self.update_status("完成")

    def export(self, fmt):
        if not self.all_results:
            messagebox.showinfo("提示", "没有可导出的数据，请先运行刮削")
            return

        if fmt == "csv":
            path = export_results_csv(self.all_results, self.start_time or datetime.now())
            messagebox.showinfo("导出成功", f"CSV已导出:\n{os.path.basename(path)}")
        else:
            folder = self.folder_var.get().strip() or "."
            path = export_results_json(self.all_results, folder, self.start_time or datetime.now())
            messagebox.showinfo("导出成功", f"JSON已导出:\n{os.path.basename(path)}")


def main():
    if sys.platform == "win32":
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
        os.system("chcp 65001 >NUL 2>&1")

    root = tk.Tk()

    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    app = JavDBApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
