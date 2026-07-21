"""The English brief must be complete: no CJK may survive translation."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_i18n import PHRASES, contains_cjk, translate_report  # noqa: E402


class PhraseTableTests(unittest.TestCase):
    def test_no_translation_contains_cjk(self) -> None:
        for zh, en in PHRASES.items():
            self.assertFalse(contains_cjk(en), f"untranslated value for {zh!r}")

    def test_longer_phrases_win_over_their_substrings(self) -> None:
        text = "市场数据不足/待确认"
        self.assertEqual(translate_report(text), "Insufficient market data / unconfirmed")

    def test_headline_layout(self) -> None:
        zh = "今日主结论：MSFT「继续持有」；GOOGL「观望但提高警戒」。市场环境为「风险偏好偏强/顺风」，大盘热度为「中性偏热」。"
        en = translate_report(zh)
        self.assertIn('Headline: MSFT "Hold"; GOOGL "Watch with raised alert".', en)
        self.assertIn('Market regime "Risk appetite strong / tailwind"', en)
        self.assertFalse(contains_cjk(en))


class EnglishDemoBriefTests(unittest.TestCase):
    """Full pipeline run; the demo is deterministic and offline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "analyze_portfolio.py"),
                "--demo",
                "--lang",
                "en",
                "--output-dir",
                cls.tmp.name,
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        )
        cls.report = result.stdout

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _template_text(self) -> str:
        # Absolute paths echo the user's environment and may legitimately
        # contain CJK (e.g. a Chinese folder name); they are not template text.
        lines = self.report.splitlines()
        return "\n".join(
            line
            for line in lines
            if str(ROOT) not in line and self.tmp.name not in line
        )

    def test_no_cjk_survives_in_template_text(self) -> None:
        text = self._template_text()
        self.assertTrue(text.strip(), "demo brief came back empty")
        offending = [line for line in text.splitlines() if contains_cjk(line)]
        self.assertEqual(offending, [], f"untranslated fragments: {offending[:5]}")

    def test_key_sections_render_in_english(self) -> None:
        for phrase in (
            "# Daily US Portfolio Research Brief",
            "## 2. Holdings Fact Table",
            "Drafted action",
            "DEMO MODE",
            "never default trade triggers",
        ):
            self.assertIn(phrase, self.report)

    def test_table_structure_is_preserved(self) -> None:
        header_rows = [
            line for line in self.report.splitlines() if line.startswith("|Ticker|")
        ]
        self.assertGreaterEqual(len(header_rows), 2)
        for row in header_rows:
            self.assertEqual(row.count("|"), row.strip().count("|"))


if __name__ == "__main__":
    unittest.main()
