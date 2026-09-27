"""前端專用入口，沿用原始策略與日期防線，隔離每次請求的輸出。"""
import os
from pathlib import Path
from trading_system import backtest

if __name__ == "__main__":
    output = os.environ.get("REPORT_DIR")
    if not output:
        raise RuntimeError("此入口必須由前端 API 提供 REPORT_DIR")
    backtest.REPORT_DIR = Path(output).resolve()
    backtest.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    backtest.main()
