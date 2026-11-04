# Technical V2-B 資料資格放寬實驗

- Experiment: `technical-v2b-data-eligibility-2303-2023-2024-v1`；股票 2303 聯電；資料角色 `historical_development`。
- 期間：2023-01-01～2024-12-31。只查詢 Training exposed 資料；沒有讀取 2025/2026、Holdout 或 Final OOS。
- A/B 均使用凍結 Technical V2-B 進出場、Risk V1、相同本金與成本；兩邊都不使用 ML Gate。
- 唯一實驗變因是資料資格：A 要五檔完整 54 根共同日；B 要 2303 自身完整 54 根，代理四檔只要求同一天各自有有效 13:25 收盤。代理仍排除 2303、四檔等權、SMA20，且只看嚴格早於訊號日的同步日期。
- 沒有補值、合成 K 棒或用未來收盤回填。2303 不完整 session 不進入成交回放；其真實 K 棒留在 missing-bar audit，並且缺口影響後 240 個預期 bar 的指標訊號會被遮蔽。持倉遇到缺棒時，在下一個可觀測開盤強制退出並記錄原因。

## 交易日資格

- A 原始全域完整日期：422 日。
- B 目標股完整日期：479 日。
- B 代理可同日同步的 13:25 收盤日期：481 日。
- B 新增可用日期：57 日。此數字代表可用於指標／候選評估的日期，不等於新增交易數。
- 新增日期上的市場濾網通過候選：14；風控後實際成交：6。

## A/B 實際績效

| 組別 | 進場資格日 | 交易數 | 報酬 | 淨損益 | 勝率 | 最大回撤 | Profit Factor | Sharpe | 成本 | 目標股實際觀察日 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_ORIGINAL_GLOBAL_COMPLETE | 402 | 39 | -0.201% | -2,006.52 | 30.76923076923077 | 0.587% | <bound method profit_factor of initial_capital                                                                  1000000.0
final_equity                                                                  997993.48125
total_return_pct                                                                 -0.200652
net_pnl                                                                        -2006.51875
realized_net_pnl                                                               -2006.51875
unrealized_pnl                                                                         0.0
win_rate_pct                                                                     30.769231
win_rate_reason                                                                       None
trade_count                                                                           39.0
max_drawdown_pct                                                                  0.587392
profit_factor                                                                     0.882206
profit_factor_reason                                                                  None
variant                                                         A_ORIGINAL_GLOBAL_COMPLETE
strategy_id                                                     A_ORIGINAL_GLOBAL_COMPLETE
strategy_version                                         technical_v2b_data_eligibility_v1
stock_code                                                                            2303
data_role                                                           historical_development
backtest_start                                                                  2023-01-01
backtest_end                                                                    2024-12-31
average_trade_net_return_pct                                                     -0.085554
sharpe_ratio                                                                     -0.225634
sharpe_reason                                                                         None
average_winning_trade_pnl                                                       1252.29875
average_losing_trade_pnl                                                       -630.892731
average_winning_trade_return_pct                                                  2.509691
average_losing_trade_return_pct                                                  -1.238996
payoff_ratio                                                                      1.984963
average_holding_bars                                                             84.666667
average_holding_trading_day_intervals                                             1.512821
median_holding_bars                                                                   80.0
median_holding_trading_day_intervals                                                   1.0
exit_holding_day_counts                  {'same_day': 7, '1_day': 14, '2_day': 9, '3_da...
stop_loss_exit_count                                                                   1.0
technical_exit_count                                                                  32.0
time_expiration_exit_count                                                             6.0
end_of_backtest_exit_count                                                             0.0
total_fee                                                                       5641.21875
total_transaction_tax                                                               5415.3
total_slippage                                                                         0.0
total_transaction_cost                                                         11056.51875
maximum_consecutive_losses                                                             6.0
annualized_volatility_pct                                                         0.463555
daily_mark_to_market_returns_count                                                   479.0
risk_metric_assumption                   daily close-to-close marked equity return; ris...
holding_day_definition                   number of observed trading-session date transi...
risk_decision_count                                                                   39.0
raw_breakout_trigger_count                                                           315.0
would_be_blocked_by_bollinger_count                                                   43.0
would_be_blocked_by_ma120_count                                                       43.0
technical_candidate_count                                                            168.0
post_market_filter_candidate_count                                                   108.0
actual_entry_fills                                                                    39.0
annual_performance                       [{'variant': 'A_ORIGINAL_GLOBAL_COMPLETE', 'ye...
arm                                                             A_ORIGINAL_GLOBAL_COMPLETE
ml_gate                                                                     NONE_BOTH_ARMS
actual_target_sessions                                                               479.0
data_gap_exit_count                                                                    0.0
open_position_at_end                                                                   0.0
data_eligible_session_count                                                          402.0
Name: 0, dtype: object> | -0.22563404542028748 | 11,056.52 | 479 |
| B_TARGET_SESSION_AND_PEER_CLOSE | 459 | 45 | -0.519% | -5,192.97 | 28.88888888888889 | 0.910% | <bound method profit_factor of initial_capital                                                                  1000000.0
final_equity                                                                 994807.028688
total_return_pct                                                                 -0.519297
net_pnl                                                                       -5192.971312
realized_net_pnl                                                              -5192.971313
unrealized_pnl                                                                         0.0
win_rate_pct                                                                     28.888889
win_rate_reason                                                                       None
trade_count                                                                           45.0
max_drawdown_pct                                                                  0.909859
profit_factor                                                                      0.75768
profit_factor_reason                                                                  None
variant                                                    B_TARGET_SESSION_AND_PEER_CLOSE
strategy_id                                                B_TARGET_SESSION_AND_PEER_CLOSE
strategy_version                                         technical_v2b_data_eligibility_v1
stock_code                                                                            2303
data_role                                                           historical_development
backtest_start                                                                  2023-01-01
backtest_end                                                                    2024-12-31
average_trade_net_return_pct                                                     -0.203955
sharpe_ratio                                                                     -0.552409
sharpe_reason                                                                         None
average_winning_trade_pnl                                                      1249.017115
average_losing_trade_pnl                                                       -669.693557
average_winning_trade_return_pct                                                  2.514592
average_losing_trade_return_pct                                                  -1.308365
payoff_ratio                                                                      1.865058
average_holding_bars                                                             82.622222
average_holding_trading_day_intervals                                             1.511111
median_holding_bars                                                                   73.0
median_holding_trading_day_intervals                                                   1.0
exit_holding_day_counts                  {'same_day': 6, '1_day': 20, '2_day': 9, '3_da...
stop_loss_exit_count                                                                   2.0
technical_exit_count                                                                  36.0
time_expiration_exit_count                                                             7.0
end_of_backtest_exit_count                                                             0.0
total_fee                                                                      6512.723813
total_transaction_tax                                                            6412.7475
total_slippage                                                                         0.0
total_transaction_cost                                                        12925.471313
maximum_consecutive_losses                                                             8.0
annualized_volatility_pct                                                         0.493642
daily_mark_to_market_returns_count                                                   479.0
risk_metric_assumption                   daily close-to-close marked equity return; ris...
holding_day_definition                   number of observed trading-session date transi...
risk_decision_count                                                                   45.0
raw_breakout_trigger_count                                                           315.0
would_be_blocked_by_bollinger_count                                                   43.0
would_be_blocked_by_ma120_count                                                       43.0
technical_candidate_count                                                            168.0
post_market_filter_candidate_count                                                   125.0
actual_entry_fills                                                                    45.0
annual_performance                       [{'variant': 'B_TARGET_SESSION_AND_PEER_CLOSE'...
arm                                                        B_TARGET_SESSION_AND_PEER_CLOSE
ml_gate                                                                     NONE_BOTH_ARMS
actual_target_sessions                                                               479.0
data_gap_exit_count                                                                    0.0
open_position_at_end                                                                   0.0
data_eligible_session_count                                                          459.0
Name: 1, dtype: object> | -0.5524088740231857 | 12,925.47 | 479 |

報酬差（B−A）=-0.319 個百分點；這是已曝光開發期間的探索性結果，不是乾淨樣本外結論，也不會據此取代凍結 V2-B。

## 資料與訊號安全檢查

- 共同時間戳 OHLCV 比對：113,940 列；欄位容差內不一致 0。
- 非法 OHLCV 列：0；重複 timestamp：0。
- 缺少預期 K 棒：370 根。均保留缺漏紀錄，未補值。
- A/B 進場訊號都要求訊號 bar 後恰有下一根實際 5 分 K；成交時間戳不連續者不建立候選成交。
- 指標窗口缺口遮蔽：最長必要窗口 240 根。代理只採相同日期四檔有效 13:25 close；缺一檔即排除該代理日期，不混用不同日期。
- 當日候選、風控決策、實際成交分開記錄。新增日的候選或成交若為 0，表示日期增加不保證訊號增加。

## 判讀與限制

- 如果 B 的有效日增加但候選／成交沒有增加，問題可能仍在技術條件或市場濾網，而不是共同日期交集。
- 如果 B 候選增加，仍需看完整交易紀錄、成本後報酬與回撤；交易數增加本身不代表訊號品質改善。
- 目標股不完整 session 保守排除，避免把不連續 bar 當成連續 5 分 K。未來若要納入這類 session，應先檢查每個訊號窗口、下一根成交 bar，以及持倉期間停損所需的缺口處理。
- Risk V1 使用既有 1% 風險預算、40% 曝險上限、2.5% 停損；策略仍有既有整股／最大數量限制。滑價為 0。
- 這是資料資格實驗，績效差異不是策略改善證據。不得因本次結果在同一期間回頭調參。

## 檔案

- `data_eligibility_comparison.csv`：日期級 A/B 資格與排除原因。
- `missing_kbar_audit.csv`：各股票缺棒、重複與非法資料 audit。
- `newly_eligible_days.csv`：B 相對 A 新增日期與代理／Warm-up 狀態。
- `candidate_trade_diff.csv`：候選訊號與成交逐筆對照。
- `performance_comparison.csv`：A/B 指標及差值。
- `audit.json`：資料範圍、來源、檢查與限制。
