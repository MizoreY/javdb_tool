"""Shared scraping, NFO, progress, and export logic for the CLI and GUI."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import random
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlencode


BASE_URL = "https://javdb.com"
SEARCH_URL = f"{BASE_URL}/search"
PROGRESS_FILE = ".javdb_progress.json"
STATE_FILE = ".javdb_state.json"


class CoreError(Exception):
    """A user-facing application error."""


@dataclass(frozen=True)
class Settings:
    proxy: str | None = "http://127.0.0.1:7890"
    delay_min: float = 8.0
    delay_max: float = 15.0
    page_wait: float = 4.0
    cf_retry_wait: float = 10.0
    cf_retry_limit: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        proxy = os.environ.get("JAVDB_PROXY", "http://127.0.0.1:7890").strip()
        try:
            delay_min = float(os.environ.get("JAVDB_DELAY_MIN", "8"))
            delay_max = float(os.environ.get("JAVDB_DELAY_MAX", "15"))
        except ValueError as exc:
            raise CoreError("请求间隔必须是数字") from exc
        if delay_min < 0 or delay_max < delay_min:
            raise CoreError("请求间隔必须满足 0 <= JAVDB_DELAY_MIN <= JAVDB_DELAY_MAX")
        return cls(proxy=proxy or None, delay_min=delay_min, delay_max=delay_max)


@dataclass
class BatchSummary:
    status: str
    total: int
    processed: int = 0
    updated_files: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[dict] = field(default_factory=list)


def normalize_code(value: str | None) -> str:
    """Normalize separators while preserving the complete identifier."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def parse_score_value(rating_str: str | None) -> float | None:
    if not rating_str:
        return None
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", rating_str)
    if not match:
        return None
    try:
        score = float(match.group(1))
    except ValueError:
        return None
    return score if 0 <= score <= 5 else None


def select_search_item(items: Iterable, code: str):
    items = list(items)
    if not items:
        return None
    expected = (code or "").strip().upper()
    for item in items:
        uid_el = item.select_one(".uid")
        if uid_el and expected in uid_el.get_text(" ", strip=True).upper():
            return item
    return items[0]


async def search_code(browser, code: str, settings: Settings | None = None) -> dict:
    settings = settings or Settings.from_env()
    result = {"code": code, "rating": None, "title": None, "url": None, "error": None}
    try:
        if browser is None:
            raise CoreError("浏览器未启动")

        page = await browser.get(f"{SEARCH_URL}?{urlencode({'q': code, 'f': 'all'})}")
        if page is None:
            raise CoreError("页面加载失败")
        await page.sleep(settings.page_wait)
        html = await page.get_content()
        if not html:
            raise CoreError("获取页面内容失败")
        if "Just a moment" in html or "challenge" in html.lower():
            raise CoreError("触发 Cloudflare 验证")

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".movie-list .item")
        target = select_search_item(items, code)
        if target is None:
            raise CoreError("未找到搜索结果")

        link_el = target.select_one("a")
        if link_el:
            href = link_el.get("href", "")
            result["url"] = BASE_URL + href if href.startswith("/") else href
        title_el = target.select_one(".video-title")
        if title_el:
            result["title"] = title_el.get_text(" ", strip=True)
        score_el = target.select_one(".score .value")
        result["rating"] = score_el.get_text(" ", strip=True) if score_el else None
        if parse_score_value(result["rating"]) is None:
            raise CoreError("没有可用评分")
    except Exception as exc:
        result["error"] = str(exc) or exc.__class__.__name__
    return result


async def start_browser(settings: Settings):
    import nodriver as uc

    common_args = ["--disable-gpu", "--disable-blink-features=AutomationControlled"]
    errors = []
    attempts = [settings.proxy, None] if settings.proxy else [None]
    for proxy in attempts:
        args = list(common_args)
        if proxy:
            args.insert(0, f"--proxy-server={proxy}")
        try:
            browser = await uc.start(headless=False, browser_args=args)
            return browser, proxy
        except Exception as exc:
            errors.append(str(exc))
    raise CoreError("浏览器启动失败: " + "；".join(errors))


def stop_browser(browser) -> None:
    if browser is None:
        return
    try:
        browser.stop()
    except Exception:
        pass


def find_nfo_files(folder: str | Path) -> list[Path]:
    root = Path(folder)
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".nfo")


def _parse_xml(path: str | Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser=parser)


def extract_code_from_nfo(nfo_path: str | Path) -> str:
    try:
        root = _parse_xml(nfo_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise CoreError(f"无法读取 XML: {exc}") from exc
    num_el = root.find("num")
    if num_el is None or not (num_el.text or "").strip():
        raise CoreError("缺少 <num> 番号")
    return num_el.text.strip().upper()


def read_criticrating(nfo_path: str | Path) -> float | None:
    try:
        root = _parse_xml(nfo_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise CoreError(f"无法读取 XML: {exc}") from exc
    element = root.find("criticrating")
    if element is None or not (element.text or "").strip():
        return None
    try:
        value = float(element.text.strip())
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def _atomic_json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoreError(f"状态文件损坏: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise CoreError(f"状态文件格式无效: {path}")
    return data


def load_progress(folder: str | Path) -> dict:
    data = _load_json(Path(folder) / PROGRESS_FILE, {"version": 1, "done": {}})
    done = data.get("done", {})
    if isinstance(done, list):
        done = {str(code): {"completed_at": None} for code in done}
    if not isinstance(done, dict):
        raise CoreError("进度文件中的 done 必须是对象或数组")
    return {"version": 1, "done": done}


def save_progress(folder: str | Path, progress: dict) -> None:
    _atomic_json_write(Path(folder) / PROGRESS_FILE, progress)


def mark_progress_done(folder: str | Path, progress: dict, code: str) -> None:
    progress.setdefault("done", {})[code] = {"completed_at": datetime.now().isoformat(timespec="seconds")}
    save_progress(folder, progress)


def clear_progress(folder: str | Path) -> None:
    try:
        (Path(folder) / PROGRESS_FILE).unlink()
    except FileNotFoundError:
        pass


def load_state(folder: str | Path) -> dict:
    data = _load_json(Path(folder) / STATE_FILE, {"version": 1, "last_success": {}})
    if not isinstance(data.get("last_success", {}), dict):
        raise CoreError("状态文件中的 last_success 必须是对象")
    return {"version": 1, "last_success": data.get("last_success", {})}


def mark_state_success(folder: str | Path, state: dict, code: str) -> None:
    state.setdefault("last_success", {})[code] = datetime.now().isoformat(timespec="seconds")
    _atomic_json_write(Path(folder) / STATE_FILE, state)


def was_updated_today(state: dict, code: str) -> bool:
    raw = state.get("last_success", {}).get(code)
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw).date() == datetime.now().date()
    except (TypeError, ValueError):
        return False


def collect_nfo_map(folder: str | Path, mode: str = "all") -> tuple[dict[str, list[Path]], list[str]]:
    if mode not in {"all", "fill"}:
        raise CoreError(f"未知模式: {mode}")
    state = load_state(folder)
    nfo_map: dict[str, list[Path]] = {}
    warnings = []
    for path in find_nfo_files(folder):
        try:
            code = extract_code_from_nfo(path)
            if mode == "fill":
                rating = read_criticrating(path)
                if rating is not None and rating >= 10 and was_updated_today(state, code):
                    continue
            nfo_map.setdefault(code, []).append(path)
        except CoreError as exc:
            warnings.append(f"{path}: {exc}")
    return nfo_map, warnings


def update_nfo_rating(nfo_path: str | Path, rating: float, keep_backup: bool = True) -> tuple[str, str]:
    path = Path(nfo_path)
    try:
        decimal_rating = Decimal(str(rating))
        if not decimal_rating.is_finite() or not 0 <= decimal_rating <= 5:
            raise CoreError(f"评分超出范围: {rating}")
        rounded_rating = decimal_rating.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        critic_rating = (decimal_rating * 20).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

        tree = _parse_xml(path)
        root = tree.getroot()
        rating_el = root.find("rating")
        old_rating = (rating_el.text or "").strip() if rating_el is not None else ""
        if rating_el is None:
            rating_el = ET.SubElement(root, "rating")
        rating_el.text = format(rounded_rating, ".1f")
        critic_el = root.find("criticrating")
        old_criticrating = (critic_el.text or "").strip() if critic_el is not None else ""
        if critic_el is None:
            critic_el = ET.SubElement(root, "criticrating")
        critic_el.text = format(critic_rating, ".1f")

        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        try:
            with open(temp_name, "wb") as stream:
                tree.write(stream, encoding="utf-8", xml_declaration=True)
                stream.flush()
                os.fsync(stream.fileno())
            shutil.copymode(path, temp_name)
            if keep_backup:
                backup_path = path.with_suffix(path.suffix + ".bak")
                backup_fd, backup_temp = tempfile.mkstemp(
                    prefix=f".{backup_path.name}.", suffix=".tmp", dir=path.parent
                )
                os.close(backup_fd)
                try:
                    shutil.copy2(path, backup_temp)
                    os.replace(backup_temp, backup_path)
                except Exception:
                    try:
                        os.unlink(backup_temp)
                    except OSError:
                        pass
                    raise
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return old_rating, old_criticrating
    except (ET.ParseError, OSError, InvalidOperation) as exc:
        raise CoreError(f"写入 NFO 失败: {exc}") from exc


async def _interruptible_sleep(seconds: float, should_stop: Callable[[], bool]) -> bool:
    remaining = seconds
    while remaining > 0:
        if should_stop():
            return False
        interval = min(0.25, remaining)
        await asyncio.sleep(interval)
        remaining -= interval
    return not should_stop()


async def process_codes(
    browser,
    codes: list[str],
    nfo_map: dict[str, list[Path]],
    folder: str | Path,
    settings: Settings,
    should_stop: Callable[[], bool],
    on_log: Callable[[str, str], None],
    on_progress: Callable[[BatchSummary, str], None],
) -> BatchSummary:
    summary = BatchSummary(status="running", total=len(codes))
    progress = load_progress(folder)
    state = load_state(folder)
    consecutive_cf = 0

    index = 0
    while index < len(codes):
        code = codes[index]
        if should_stop():
            summary.status = "cancelled"
            break
        on_progress(summary, code)
        result = await search_code(browser, code, settings)

        if "Cloudflare" in (result.get("error") or ""):
            consecutive_cf += 1
            if consecutive_cf >= settings.cf_retry_limit:
                summary.status = "needs_verification"
                summary.failed += 1
                summary.results.append({**result, "files": [str(p) for p in nfo_map.get(code, [])]})
                on_log("连续触发 Cloudflare 验证，进度已保留，请验证后重新开始", "error")
                break
            on_log(f"{code} 触发 Cloudflare 验证，稍后重试 ({consecutive_cf}/{settings.cf_retry_limit})", "warning")
            if not await _interruptible_sleep(settings.cf_retry_wait, should_stop):
                summary.status = "cancelled"
                break
            continue

        consecutive_cf = 0
        files = nfo_map.get(code, [])
        result["files"] = [str(path) for path in files]
        summary.results.append(result)
        summary.processed += 1

        if result.get("error"):
            summary.failed += 1
            on_log(f"{code}: {result['error']}", "error")
        else:
            score = parse_score_value(result.get("rating"))
            file_errors = []
            for path in files:
                try:
                    old_rating, old_criticrating = update_nfo_rating(path, score)
                    summary.updated_files += 1
                    new_rating = Decimal(str(score)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                    new_criticrating = (Decimal(str(score)) * 20).quantize(
                        Decimal("0.1"), rounding=ROUND_HALF_UP
                    )
                    on_log(
                        f"{code}: rating {old_rating or '-'} -> {new_rating:.1f}, "
                        f"criticrating {old_criticrating or '-'} -> {new_criticrating:.1f}  {path.name}",
                        "success",
                    )
                except CoreError as exc:
                    file_errors.append(f"{path}: {exc}")
            if file_errors:
                summary.failed += 1
                result["error"] = "；".join(file_errors)
                for message in file_errors:
                    on_log(message, "error")
            else:
                try:
                    mark_progress_done(folder, progress, code)
                except OSError as exc:
                    summary.failed += 1
                    result["error"] = f"保存进度失败: {exc}"
                    on_log(result["error"], "error")
                else:
                    try:
                        mark_state_success(folder, state, code)
                    except OSError as exc:
                        on_log(f"保存每日状态失败，下次补全可能重复查询: {exc}", "warning")

        on_progress(summary, code)
        index += 1
        if index < len(codes):
            delay = random.uniform(settings.delay_min, settings.delay_max)
            if not await _interruptible_sleep(delay, should_stop):
                summary.status = "cancelled"
                break

    if summary.status == "running":
        summary.status = "completed" if summary.failed == 0 else "partial"

    if summary.status == "completed":
        clear_progress(folder)
    return summary


def pending_codes(folder: str | Path, nfo_map: dict[str, list[Path]]) -> tuple[list[str], int]:
    progress = load_progress(folder)
    done = set(progress.get("done", {}))
    return [code for code in nfo_map if code not in done], len(done.intersection(nfo_map))


def export_results(results: list[dict], folder: str | Path, fmt: str, start_time: datetime) -> Path:
    output_dir = Path(folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / start_time.strftime(f"javdb_results_%Y%m%d_%H%M%S.{fmt}")
    if fmt == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["番号", "评分", "标题", "链接", "状态", "文件路径"])
            for item in results:
                writer.writerow([_csv_safe(value) for value in [
                    item.get("code", ""), item.get("rating", ""), item.get("title", ""),
                    item.get("url", ""), item.get("error") or "成功", "; ".join(item.get("files", [])),
                ]])
    elif fmt == "json":
        _atomic_json_write(path, {
            "folder": str(folder),
            "export_time": start_time.isoformat(timespec="seconds"),
            "total": len(results),
            "results": results,
        })
    else:
        raise CoreError(f"不支持的导出格式: {fmt}")
    return path


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text
