"""相容入口；報表匯入工具已移至 scripts/import_existing_report.py。"""

from scripts.import_existing_report import *  # noqa: F401,F403
from scripts.import_existing_report import main


if __name__ == "__main__":
    main()
