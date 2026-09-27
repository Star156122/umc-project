"""相容入口；資料更新工具已移至 scripts/update_data.py。"""

from scripts.update_data import *  # noqa: F401,F403
from scripts.update_data import main


if __name__ == "__main__":
    main()
