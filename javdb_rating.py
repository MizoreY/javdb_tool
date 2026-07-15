import asyncio
import sys
import os
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


def setup_console():
    if sys.platform == "win32":
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
        os.system("chcp 65001 >NUL 2>&1")


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
        if browser is None:
            result["error"] = "浏览器未启动"
            return result

        url = f"{SEARCH_URL}?q={code}&f=all"
        page = await browser.get(url)
        if page is None:
            result["error"] = "页面加载失败"
            return result

        await page.sleep(4)

        html = await page.get_content()
        if html is None:
            result["error"] = "获取页面内容失败"
            return result

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


def export_results_csv(results, folder, start_time):
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


async def run_search(codes, on_result=None):
    browser = None
    try:
        print("  正在启动浏览器（使用代理 %s）..." % PROXY)
        sys.stdout.flush()
        browser = await uc.start(
            headless=False,
            browser_args=[
                "--proxy-server=%s" % PROXY,
                "--disable-gpu",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
    except Exception as e:
        print("  浏览器启动失败: %s" % e)
        print("  尝试不使用代理启动...")
        sys.stdout.flush()
        try:
            browser = await uc.start(
                headless=False,
                browser_args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        except Exception as e2:
            print("  浏览器启动失败: %s" % e2)
            return []

    if browser is None:
        print("错误: 浏览器启动失败，请检查 Chrome 是否正确安装")
        return []

    print("  浏览器启动成功！")
    sys.stdout.flush()

    results = []
    banned = False
    consecutive_fail = 0
    consecutive_cf = 0
    i = 0
    while i < len(codes):
        code = codes[i]
        if banned:
            results.append({"code": code, "rating": None, "title": None, "url": None, "error": "已暂停"})
            i += 1
            continue

        print("  正在查询: %s (%d/%d) ..." % (code, i + 1, len(codes)))
        sys.stdout.flush()

        res = await search_code(browser, code)

        if "Cloudflare" in (res["error"] or ""):
            consecutive_cf += 1
            if consecutive_cf >= 3:
                results.append(res)
                print("  %-15s [%s]" % (code, res["error"]))
                print()
                print("  " + "!" * 55)
                print("  !  警告: 连续%d次触发Cloudflare验证，可能已被封禁！" % consecutive_cf)
                print("  !  后续查询已暂停")
                print("  " + "!" * 55)
                print()
                print("  请在浏览器中手动验证: https://javdb.com")
                print("  确认恢复后输入 c 继续，输入 q 退出")
                print()
                sys.stdout.flush()

                banned = True
                while True:
                    try:
                        cmd = input("  请输入指令 (c/q): ").strip().lower()
                    except EOFError:
                        cmd = "q"
                    if cmd == "c":
                        print("  继续查询...\n")
                        banned = False
                        consecutive_cf = 0
                        results.pop()
                        break
                    elif cmd == "q":
                        print("  已停止查询。")
                        if browser:
                            try:
                                browser.stop()
                            except:
                                pass
                        return results
                    else:
                        print("  无效输入，请输入 c 继续或 q 退出")
            else:
                print("  %-15s 触发Cloudflare验证，10秒后重试... (%d/3)" % (code, consecutive_cf))
                sys.stdout.flush()
                await asyncio.sleep(10)
            continue

        consecutive_cf = 0
        results.append(res)

        if res["error"]:
            rating_str = "[%s]" % res["error"]
        else:
            rating_str = res["rating"] or "无评分"
        title_str = (res["title"] or "")[:50]
        print("  %-15s %-12s %s" % (res["code"], rating_str, title_str))
        sys.stdout.flush()

        score = parse_score_value(res["rating"])
        is_fail = False

        if res["error"] == "未找到结果":
            consecutive_fail += 1
            if consecutive_fail >= 3:
                is_fail = True
        else:
            consecutive_fail = 0

        if is_fail:
            reason = "连续%d次查询异常" % consecutive_fail

            banned = True
            print()
            print("  " + "!" * 55)
            print("  !  警告: 可能已被封禁！")
            print("  !  原因: %s" % reason)
            print("  !  后续查询已暂停")
            print("  " + "!" * 55)
            print()
            print("  请在浏览器中手动验证: https://javdb.com")
            print("  确认恢复后输入 c 继续，输入 q 退出")
            print()
            sys.stdout.flush()

            while True:
                try:
                    cmd = input("  请输入指令 (c/q): ").strip().lower()
                except EOFError:
                    cmd = "q"
                if cmd == "c":
                    print("  继续查询...\n")
                    banned = False
                    break
                elif cmd == "q":
                    print("  已停止查询。")
                    if browser:
                        try:
                            browser.stop()
                        except:
                            pass
                    return results
                else:
                    print("  无效输入，请输入 c 继续或 q 退出")

            if results and results[-1]["code"] == code:
                results.pop()
            i -= 1
            consecutive_fail = 0

            if i < len(codes) - 1:
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                print("  等待 %.1f 秒后继续..." % delay)
                sys.stdout.flush()
                await asyncio.sleep(delay)
            i += 1
            continue

        if on_result:
            on_result(res, score)

        i += 1

        if i < len(codes) - 1:
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            print("  等待 %.1f 秒后继续..." % delay)
            sys.stdout.flush()
            await asyncio.sleep(delay)

    if browser:
        try:
            browser.stop()
        except:
            pass
    return results


def run_and_export(folder, nfo_map, query_codes, progress, prompt):
    if not query_codes:
        print("所有番号均已完成，无需查询。")
        return

    print("\n启动浏览器查询 %d 个番号...\n" % len(query_codes))
    print("%-15s %-12s %s" % ("番号", "评分", "标题"))
    print("-" * 65)

    start_time = datetime.now()
    stats = {"updated": 0, "failed": 0}
    all_results = []

    def on_result(res, score):
        if score is None:
            return

        rating_val = round(score, 1)
        critic_rating = round(score * 20, 1)
        all_success = True
        for nfo_path in nfo_map.get(res["code"], []):
            try:
                result, old_rating = update_nfo_rating(nfo_path, score, rating_val, critic_rating)
                if result is True:
                    print("  [已更新] %s -> rating=%.1f, criticrating=%.1f  %s" % (
                        res["code"], rating_val, critic_rating, os.path.basename(nfo_path)))
                    stats["updated"] += 1
                else:
                    print("  [失败] %s - %s" % (res["code"], result))
                    stats["failed"] += 1
                    all_success = False
            except Exception as e:
                print("  [失败] %s - %s" % (res["code"], e))
                stats["failed"] += 1
                all_success = False

        if all_success and res["code"] not in progress.get("done", []):
            progress.setdefault("done", []).append(res["code"])
            save_progress(folder, progress)

        sys.stdout.flush()

    try:
        results = asyncio.run(run_search(query_codes, on_result=on_result))
    except KeyboardInterrupt:
        print("\n\n用户中断，进度已保存。下次运行可选择续传。")
        return
    except Exception as e:
        print("\n查询出错: %s" % e)
        print("进度已保存，下次运行可选择续传。")
        return

    for res in results:
        code = res.get("code", "")
        files = [os.path.basename(p) for p in nfo_map.get(code, [])]
        all_results.append({**res, "files": files})

    clear_progress(folder)

    print("\n" + "=" * 65)
    print("  完成! 更新: %d, 失败: %d" % (stats["updated"], stats["failed"]))
    print("=" * 65)

    if all_results:
        print()
        print("  是否导出查询结果？")
        print("  c - 导出为CSV格式")
        print("  j - 导出为JSON格式")
        print("  b - 同时导出CSV和JSON")
        print("  n - 不导出")
        print()
        export_choice = input("请选择 (c/j/b/n): ").strip().lower()
        
        if export_choice == "c":
            csv_path = export_results_csv(all_results, folder, start_time)
            print("\nCSV已导出: %s" % os.path.basename(csv_path))
        elif export_choice == "j":
            json_path = export_results_json(all_results, folder, start_time)
            print("\nJSON已导出: %s" % os.path.basename(json_path))
        elif export_choice == "b":
            csv_path = export_results_csv(all_results, folder, start_time)
            json_path = export_results_json(all_results, folder, start_time)
            print("\nCSV已导出: %s" % os.path.basename(csv_path))
            print("JSON已导出: %s" % os.path.basename(json_path))


def select_and_run(folder, nfo_map, prompt):
    unique_codes = list(nfo_map.keys())

    if not unique_codes:
        print("未找到需要查询的番号。")
        return

    progress = load_progress(folder)
    done_codes = set(progress.get("done", []))
    resume_codes = [c for c in unique_codes if c in done_codes]
    pending_codes = [c for c in unique_codes if c not in done_codes]

    print("=" * 65)
    print("  共发现 %d 个番号：" % len(unique_codes))
    if resume_codes:
        print("  (其中 %d 个已有进度记录)" % len(resume_codes))
    print("=" * 65)

    for i, code in enumerate(unique_codes, 1):
        paths = nfo_map[code]
        tag = " [已完成]" if code in done_codes else ""
        print("  %2d. %-15s %s%s" % (i, code, os.path.basename(paths[0]), tag))
        for extra in paths[1:]:
            print("      %-15s %s" % ("", os.path.basename(extra)))

    query_codes = unique_codes
    if resume_codes:
        print()
        print("  a - 全部重新查询（%d 个）" % len(unique_codes))
        print("  r - 跳过已完成，只查询剩余 %d 个" % len(pending_codes))
        print("  n - 取消")
        print()
        choice = input("请选择 (a/r/n): ").strip().lower()
        if choice == "n":
            print("已取消。")
            return
        elif choice == "r":
            query_codes = pending_codes
            clear_progress(folder)
            progress = {"done": list(done_codes)}
            save_progress(folder, progress)
        else:
            clear_progress(folder)
            progress = {"done": []}
    else:
        print()
        confirm = input(prompt).strip().lower()
        if confirm != "y":
            print("已取消。")
            return

    run_and_export(folder, nfo_map, query_codes, progress, prompt)


def mode_folder():
    print()
    folder = input("请输入文件夹路径: ").strip().strip('"')

    if not os.path.isdir(folder):
        print("文件夹不存在: %s" % folder)
        return

    nfo_files = find_nfo_files(folder)
    if not nfo_files:
        print("未找到任何 .nfo 文件。")
        return

    print("\n找到 %d 个 .nfo 文件，正在提取番号...\n" % len(nfo_files))

    nfo_map = {}
    skipped_files = []
    for nfo_path in nfo_files:
        code = extract_code_from_nfo(nfo_path)
        if code:
            if code not in nfo_map:
                nfo_map[code] = []
            nfo_map[code].append(nfo_path)
        else:
            skipped_files.append(nfo_path)

    if skipped_files:
        print("\n  以下 %d 个文件无法提取番号，已跳过：" % len(skipped_files))
        for p in skipped_files:
            print("    - %s" % os.path.basename(p))

    select_and_run(folder, nfo_map, "是否开始刮削评分？(y/n): ")


def mode_fill():
    print()
    folder = input("请输入文件夹路径: ").strip().strip('"')

    if not os.path.isdir(folder):
        print("文件夹不存在: %s" % folder)
        return

    nfo_files = find_nfo_files(folder)
    if not nfo_files:
        print("未找到任何 .nfo 文件。")
        return

    print("\n找到 %d 个 .nfo 文件，正在筛选未刮削的文件...\n" % len(nfo_files))

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    nfo_map = {}
    skipped_files = []
    for nfo_path in nfo_files:
        mtime = os.path.getmtime(nfo_path)
        is_stale = mtime < today_start
        is_low_rating = check_criticrating(nfo_path)
        if not is_stale and not is_low_rating:
            continue
        code = extract_code_from_nfo(nfo_path)
        if code:
            if code not in nfo_map:
                nfo_map[code] = []
            nfo_map[code].append(nfo_path)
        else:
            skipped_files.append(nfo_path)

    if skipped_files:
        print("\n  以下 %d 个文件无法提取番号，已跳过：" % len(skipped_files))
        for p in skipped_files:
            print("    - %s" % os.path.basename(p))

    select_and_run(folder, nfo_map, "是否开始补全评分？(y/n): ")


def main():
    setup_console()

    print("=" * 65)
    print("  JavDB 番号评分刮削工具")
    print("=" * 65)
    print()
    print("  1 - 全量扫描模式（扫描文件夹内所有番号）")
    print("  2 - 补全模式（补全今日未更新或评分缺失的番号）")
    print()

    choice = input("请选择模式 (1/2): ").strip()

    try:
        if choice == "2":
            mode_fill()
        else:
            mode_folder()
    except Exception as e:
        print("\n发生错误: %s" % e)
        import traceback
        traceback.print_exc()

    print("\n查询完毕。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n发生错误: %s" % e)
        import traceback
        traceback.print_exc()
    except KeyboardInterrupt:
        pass
    finally:
        input("\n按回车键退出...")
