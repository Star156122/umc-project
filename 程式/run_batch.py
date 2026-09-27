"""相容入口；批次工具已移至 scripts/run_batch.py。"""

from scripts.run_batch import *  # noqa: F401,F403
from scripts.run_batch import main


if __name__ == "__main__":
    main()
