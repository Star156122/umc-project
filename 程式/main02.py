"""舊版相容入口；新程式請由 main.py 或 trading_system.backtest 匯入。"""

from trading_system import backtest as _implementation

# 舊測試與組員程式可能仍會使用底線開頭的內部函式，因此相容層完整轉出。
globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not (name.startswith("__") and name.endswith("__"))
    }
)

main = _implementation.main


if __name__ == "__main__":
    main()
