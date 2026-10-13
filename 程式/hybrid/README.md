# Hybrid

本資料夾集中管理 Technical × ML Hybrid 的設定、核心邏輯、執行入口與測試。

Hybrid 在專題中的角色：

```text
Technical Indicators
→ BUY Candidate

ML
→ BUY / HOLD / SELL Filter

Hybrid
→ 決定是否 ENTRY
```

## 資料夾

- `config/`：Hybrid 設定。
- `core/`：Hybrid 核心邏輯與報告呈現，包括時間對齊、Entry Gate 與使用者版 HTML。
- `scripts/`：Hybrid 執行入口。
- `tests/`：Hybrid 專用測試。
- `exports/`：成果不放在本資料夾；仍統一輸出至專案根目錄的 `exports/`。

## 執行

```powershell
uv run python hybrid/scripts/run_hybrid_poc_v1.py
```

每次執行會在同一個 `exports/hybrid_poc_v1_<timestamp>/` 輸出：

- `hybrid_poc_report.html`：研究／技術完整版。
- `hybrid_poc_user_report.html`：一般使用者簡化版。

如果既有執行資料只缺使用者版報告，可用下列指令補建，不會重跑模型或回測：

```powershell
uv run python hybrid/scripts/run_hybrid_poc_v1.py --user-report-only exports/hybrid_poc_v1_<timestamp>
```

## 測試

```powershell
uv run pytest hybrid/tests/test_hybrid_poc_v1.py -q
```

目前 `Hybrid POC V1` 僅用於流程驗證，不代表正式 Final OOS 結果。
