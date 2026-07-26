import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import javdb_core as core


class FakeText:
    def __init__(self, text):
        self.text = text

    def get_text(self, separator=" ", strip=False):
        return self.text.strip() if strip else self.text


class FakeItem:
    def __init__(self, uid):
        self.uid = FakeText(uid)

    def select_one(self, selector):
        return self.uid if selector == ".uid" else None


def write_nfo(path: Path, code="ABC-123", rating="1.0", critic="20.0"):
    path.write_text(
        f"<?xml version='1.0' encoding='utf-8'?><movie><num>{code}</num>"
        f"<rating>{rating}</rating><criticrating>{critic}</criticrating></movie>",
        encoding="utf-8",
    )


class ParsingTests(unittest.TestCase):
    def test_search_item_prefers_contains_match(self):
        items = [FakeItem("XYZ-999"), FakeItem("ABC-1234")]
        self.assertIs(items[1], core.select_search_item(items, "ABC-123"))

    def test_search_item_falls_back_to_first_result(self):
        items = [FakeItem("XYZ-999"), FakeItem("DEF-456")]
        self.assertIs(items[0], core.select_search_item(items, "ABC-123"))
        self.assertIsNone(core.select_search_item([], "ABC-123"))

    def test_score_validation(self):
        self.assertEqual(4.35, core.parse_score_value("4.35分"))
        self.assertIsNone(core.parse_score_value("暂无评分"))
        self.assertIsNone(core.parse_score_value("9.2分"))
        self.assertIsNone(core.parse_score_value("4..2"))


class FileTests(unittest.TestCase):
    def test_nfo_update_is_valid_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movie.nfo"
            write_nfo(path)
            original = path.read_bytes()

            old_rating, old_criticrating = core.update_nfo_rating(path, 4.3)

            self.assertEqual("1.0", old_rating)
            self.assertEqual("20.0", old_criticrating)
            self.assertEqual(original, path.with_suffix(".nfo.bak").read_bytes())
            root = core.ET.parse(path).getroot()
            self.assertEqual("4.3", root.findtext("rating"))
            self.assertEqual("86.0", root.findtext("criticrating"))

    def test_nfo_update_rounds_half_up_and_preserves_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movie.nfo"
            path.write_text(
                "<?xml version='1.0' encoding='utf-8'?><movie><!--keep--><num>ABC-123</num></movie>",
                encoding="utf-8",
            )

            core.update_nfo_rating(path, 4.35)

            updated = path.read_text(encoding="utf-8")
            self.assertIn("<!--keep-->", updated)
            root = core._parse_xml(path).getroot()
            self.assertEqual("4.4", root.findtext("rating"))
            self.assertEqual("87.0", root.findtext("criticrating"))

    def test_legacy_progress_is_migrated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / core.PROGRESS_FILE
            path.write_text(json.dumps({"done": ["ABC-123"]}), encoding="utf-8")
            progress = core.load_progress(folder)
            self.assertIn("ABC-123", progress["done"])

    def test_fill_mode_uses_persistent_success_state(self):
        with tempfile.TemporaryDirectory() as folder:
            nfo = Path(folder) / "movie.nfo"
            write_nfo(nfo, rating="4.0", critic="80.0")
            state = core.load_state(folder)
            core.mark_state_success(folder, state, "ABC-123")

            nfo_map, warnings = core.collect_nfo_map(folder, "fill")

            self.assertEqual({}, nfo_map)
            self.assertEqual([], warnings)

    def test_csv_export_escapes_spreadsheet_formulas(self):
        with tempfile.TemporaryDirectory() as folder:
            path = core.export_results(
                [{"code": "ABC-123", "rating": "4.0", "title": "=RUN()", "files": []}],
                folder,
                "csv",
                core.datetime(2026, 1, 2, 3, 4, 5),
            )
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual("'=RUN()", rows[1][2])


class BatchTests(unittest.TestCase):
    def run_batch(self, folder, codes, nfo_map, responses):
        settings = core.Settings(proxy=None, delay_min=0, delay_max=0, page_wait=0, cf_retry_wait=0)
        logs = []
        search = AsyncMock(side_effect=responses)
        with patch("javdb_core.search_code", search):
            summary = asyncio.run(
                core.process_codes(
                    object(),
                    codes,
                    nfo_map,
                    folder,
                    settings,
                    lambda: False,
                    lambda message, tag: logs.append((message, tag)),
                    lambda summary, code: None,
                )
            )
        return summary, search, logs

    def test_partial_run_keeps_progress_and_does_not_mark_failure_done(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.nfo"
            second = Path(folder) / "second.nfo"
            write_nfo(first, "ABC-123")
            write_nfo(second, "XYZ-999")
            responses = [
                {"code": "ABC-123", "rating": "4.0", "title": "A", "url": "u", "error": None},
                {"code": "XYZ-999", "rating": None, "title": None, "url": None, "error": "网络错误"},
            ]

            summary, _, logs = self.run_batch(
                folder,
                ["ABC-123", "XYZ-999"],
                {"ABC-123": [first], "XYZ-999": [second]},
                responses,
            )

            self.assertEqual("partial", summary.status)
            progress = core.load_progress(folder)
            self.assertIn("ABC-123", progress["done"])
            self.assertNotIn("XYZ-999", progress["done"])
            self.assertTrue(
                any(
                    "rating 1.0 -> 4.0, criticrating 20.0 -> 80.0" in message
                    for message, tag in logs
                    if tag == "success"
                )
            )

    def test_cloudflare_retries_the_same_code(self):
        with tempfile.TemporaryDirectory() as folder:
            nfo = Path(folder) / "movie.nfo"
            write_nfo(nfo)
            responses = [
                {"code": "ABC-123", "rating": None, "title": None, "url": None, "error": "触发 Cloudflare 验证"},
                {"code": "ABC-123", "rating": "4.2", "title": "A", "url": "u", "error": None},
            ]

            summary, search, _ = self.run_batch(folder, ["ABC-123"], {"ABC-123": [nfo]}, responses)

            self.assertEqual("completed", summary.status)
            self.assertEqual(2, search.await_count)
            self.assertFalse((Path(folder) / core.PROGRESS_FILE).exists())


if __name__ == "__main__":
    unittest.main()
