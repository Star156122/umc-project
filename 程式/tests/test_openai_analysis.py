"""OpenAI 分析模組的離線測試；不會呼叫網路或產生 API 費用。"""

from __future__ import annotations

import unittest

from trading_system.llm_analysis import build_analysis_payload, render_analysis_html, replace_analysis_section


class OpenAIAnalysisTests(unittest.TestCase):
    def test_payload_limits_rows_and_uses_latest_indicator(self) -> None:
        summary = {"stock_code": "2303", "strategy": "ma", "net_pnl": -100.0}
        trades = [{"id": index} for index in range(35)]
        signals = [{"id": index} for index in range(40)]
        kbars = [{"close": 50.0}, {"close": 51.0, "rsi": 62.0}]

        payload = build_analysis_payload(summary, trades, signals, kbars)

        self.assertEqual(len(payload["recent_completed_trades"]), 30)
        self.assertEqual(payload["recent_completed_trades"][0]["id"], 5)
        self.assertEqual(len(payload["recent_signals"]), 30)
        self.assertEqual(payload["latest_indicator_snapshot"]["close"], 51.0)
        self.assertIsNone(payload["news_or_event_context"])

    def test_html_escapes_model_output(self) -> None:
        result = {
            "status": "ok",
            "model": "test-model",
            "generated_at": "2026-08-24T12:00:00",
            "usage": {"total_tokens": 10},
            "analysis": {
                "executive_summary": "<script>alert(1)</script>",
                "limitations": ["樣本少"],
                "risk_notice": "僅供研究",
            },
        }

        rendered = render_analysis_html(result)

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("OPENAI_LLM_SECTION_START", rendered)

    def test_replace_analysis_section_is_idempotent(self) -> None:
        anchor = (
            '<main>\n    <div class="d-flex align-items-center section-title">\n'
            '      <i class="ri-file-list-3-fill me-2"></i>\n內容</main>'
        )
        first = replace_analysis_section(anchor, render_analysis_html(None))
        second = replace_analysis_section(first, render_analysis_html(None, "測試錯誤"))

        self.assertEqual(second.count("OPENAI_LLM_SECTION_START"), 1)
        self.assertIn("測試錯誤", second)


if __name__ == "__main__":
    unittest.main()
