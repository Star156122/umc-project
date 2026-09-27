"""Compatibility entry point.

The maintained single-file program is main02.py. This wrapper exists only so
older commands that run `python main.py` still work.
"""

from __future__ import annotations

from main02 import main


if __name__ == "__main__":
    main()
