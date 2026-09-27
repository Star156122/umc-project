"""相容入口；報表 LLM 補寫工具已移至 scripts/enrich_existing_reports.py。"""

from scripts.enrich_existing_reports import *  # noqa: F401,F403
from scripts.enrich_existing_reports import main


if __name__ == "__main__":
    main()
