import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { apiRequest } from "../src/config/api";
import { marketColors } from "../styles/marketTheme";

import HtmlReportPanel from "../components/HtmlReportPanel";

const STRATEGY_OPTIONS = [
  { key: "ma", label: "MA", runnable: true },
  { key: "rsi", label: "RSI", runnable: true },
  { key: "macd", label: "MACD", runnable: true },
  { key: "bollinger", label: "布林", runnable: true },
  { key: "breakout", label: "突破", runnable: true },
  { key: "vote", label: "多數決", runnable: true },
];

function formatNumber(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "—";
  }

  return number.toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "—";
  }

  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toLocaleString("zh-TW", {
    maximumFractionDigits: 0,
  })}`;
}

function MetricCard({ icon, label, value, tone = "normal" }) {
  const toneStyle =
    tone === "positive"
      ? styles.positiveText
      : tone === "negative"
      ? styles.negativeText
      : styles.metricValue;

  return (
    <View style={styles.metricCard}>
      <Ionicons name={icon} size={22} color={marketColors.primary} />
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, toneStyle]}>{value}</Text>
    </View>
  );
}

function SectionHeader({ icon, title }) {
  return (
    <View style={styles.sectionHeader}>
      <Ionicons name={icon} size={22} color={marketColors.primary} />
      <Text style={styles.sectionTitle}>{title}</Text>
    </View>
  );
}

function EmptyState({ message }) {
  return (
    <View style={styles.emptyCard}>
      <Ionicons name="document-text-outline" size={42} color={marketColors.textMuted} />
      <Text style={styles.emptyText}>{message}</Text>
    </View>
  );
}

export default function AnalysisScreen() {
  const [inputCode, setInputCode] = useState("2303");
  const [activeCode, setActiveCode] = useState("2303");
  const [strategy, setStrategy] = useState("ma");
  const [report, setReport] = useState(null);
  const [viewMode, setViewMode] = useState("summary");
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const requestSerial = useRef(0);
  const [loading, setLoading] = useState(true);
  const [runLoading, setRunLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("info");
  const [loadingText, setLoadingText] = useState("正在從畢業專題資料庫讀取回測結果…");

  const selectedStrategy = useMemo(
    () => STRATEGY_OPTIONS.find((item) => item.key === strategy),
    [strategy]
  );

  const loadReport = useCallback(
    async (code, isRefresh = false, strategyKey = strategy) => {
      const cleanCode = String(code || "").trim();

      if (!/^\d{4,6}$/.test(cleanCode)) {
        setMessageType("error");
        setMessage("請輸入 4～6 位數字的股票代號，例如 2303");
        return;
      }

      const requestId = ++requestSerial.current;
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
        setLoadingText("正在從畢業專題資料庫讀取已儲存的回測結果…");
      }

      setMessage("");

      try {
        const query = strategyKey
          ? `?strategy=${encodeURIComponent(strategyKey)}`
          : "";
        const data = await apiRequest(
          `/api/reports/latest/${encodeURIComponent(cleanCode)}${query}`
        );
        if (requestId !== requestSerial.current) return;
        setReport(data.report);
        setViewMode(data.report.hasHtml ? "html" : "summary");
        setActiveCode(cleanCode);
        setMessageType("success");
        setMessage(
          `已讀取 ${cleanCode} ${data.report.stockName || ""} 的 ${String(strategyKey).toUpperCase()} 結果`
        );
      } catch (error) {
        if (requestId !== requestSerial.current) return;
        setReport(null);
        setMessageType("error");
        setMessage(error.message || "讀取資料庫回測報告失敗");
      } finally {
        if (requestId === requestSerial.current) { setLoading(false); setRefreshing(false); }
      }
    },
    [strategy]
  );

  const runBacktest = useCallback(async () => {
    const cleanCode = String(inputCode || "").trim();

    if (!/^\d{4,6}$/.test(cleanCode)) {
      setMessageType("error");
      setMessage("請輸入 4～6 位數字的股票代號，例如 2330");
      return;
    }

    const option = STRATEGY_OPTIONS.find((item) => item.key === strategy);
    if (!option?.runnable) {
      await loadReport(cleanCode, false, strategy);
      return;
    }

    setRunLoading(true);
    setLoading(true);
    setLoadingText(
      `正在執行 ${cleanCode} 的 ${option.label} 回測；完成後會直接寫入畢業專題資料庫…`
    );
    setMessage("");
    setMessageType("info");

    try {
      const data = await apiRequest("/api/backtests/run", {
        method: "POST",
        body: JSON.stringify({ code: cleanCode, strategy }),
        timeoutMs: 17_200_000,
      });

      setReport(data.report);
      setViewMode(data.report.hasHtml ? "html" : "summary");
      setActiveCode(cleanCode);
      setMessageType("success");
      setMessage(
        `${data.message}（分析編號 ${data.analysisId ?? "—"}）`
      );
    } catch (error) {
      setReport(null);
      setMessageType("error");
      setMessage(
        error?.data?.details
          ? `${error.message}\n${error.data.details}`
          : error.message || "執行回測失敗"
      );
    } finally {
      setRunLoading(false);
      setLoading(false);
    }
  }, [inputCode, strategy, loadReport]);

  useEffect(() => {
    loadReport("2303", false, "ma");
  }, []);

  const summary = report?.summary;
  const latestSignal = report?.latestSignal;
  const canShowHtmlReport = Boolean(report?.hasHtml);

  const resultTone = useMemo(() => {
    if (!summary) {
      return "normal";
    }
    return summary.totalNetPnl >= 0 ? "positive" : "negative";
  }, [summary]);

  useEffect(() => {
    if (viewMode === "html" && !canShowHtmlReport) {
      setViewMode("summary");
    }
  }, [canShowHtmlReport, viewMode]);

  useEffect(() => {
    let current = true;
    setHistory([]); setHistoryError("");
    if (!report?.code) return () => { current = false; };
    apiRequest(`/api/reports/history/${report.code}?strategy=${encodeURIComponent(report.strategyKey || report.strategy)}`)
      .then(data => { if (current) setHistory(data.reports); })
      .catch(error => { if (current) setHistoryError(error.message); });
    return () => { current = false; };
  }, [report?.analysisId]);

  const selectHistory = async (analysisId) => {
    const requestId = ++requestSerial.current;
    setLoading(true); setLoadingText("正在讀取選擇的歷史報告…");
    try {
      const data = await apiRequest(`/api/reports/analysis/${analysisId}`);
      if (requestId !== requestSerial.current) return;
      setReport(data.report);
      setViewMode(data.report.hasHtml ? "html" : "summary");
      setMessage("");
    } catch (error) {
      if (requestId === requestSerial.current) { setMessageType("error"); setMessage(error.message); }
    } finally { if (requestId === requestSerial.current) setLoading(false); }
  };
  const stockTitle = report ? `${report.code}${report.stockName ? " " + report.stockName : ""}` : "";

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={[styles.content, report && viewMode === "html" && { maxWidth: 1280 }]}
      keyboardShouldPersistTaps="handled"
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => loadReport(activeCode, true, strategy)}
          tintColor={marketColors.primary}
        />
      }
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.headerRow}>
        <View style={styles.logoBox}>
          <Ionicons name="analytics" size={30} color={marketColors.white} />
        </View>
        <View style={styles.headerTextBox}>
          <Text style={styles.title}>回測分析報告</Text>
          <Text style={styles.subtitle}>查看股票歷史回測、績效摘要與原始互動報告</Text>
        </View>
      </View>

      <View style={styles.searchCard}>
        <Text style={styles.searchLabel}>回測策略</Text>
        <View style={styles.strategyRow}>
          {STRATEGY_OPTIONS.map((option) => (
            <Pressable
              key={option.key}
              style={[
                styles.strategyButton,
                strategy === option.key && styles.strategyButtonActive,
              ]}
              onPress={() => { setStrategy(option.key); loadReport(inputCode, false, option.key); }}
              disabled={runLoading}
            >
              <Text
                style={[
                  styles.strategyButtonText,
                  strategy === option.key && styles.strategyButtonTextActive,
                ]}
              >
                {option.label}
              </Text>
            </Pressable>
          ))}
        </View>

        {!selectedStrategy?.runnable ? (
          <View style={styles.strategyHintBox}>
            <Ionicons name="information-circle-outline" size={18} color={marketColors.warning} />
            <Text style={styles.strategyHint}>
              新版聯電回測引擎支援六種策略。
            </Text>
          </View>
        ) : null}

        <Text style={styles.searchLabel}>股票代號（2303 聯電、2330 台積電）</Text>
        <View style={styles.searchRow}>
          <View style={styles.inputBox}>
            <Ionicons name="search" size={21} color={marketColors.textMuted} />
            <TextInput
              style={styles.input}
              value={inputCode}
              onChangeText={setInputCode}
              placeholder="例如 2303（聯電）"
              placeholderTextColor={marketColors.textSubtle}
              keyboardType="number-pad"
              returnKeyType="search"
              editable={!runLoading}
              onSubmitEditing={runBacktest}
            />
          </View>
        </View>

        <View style={styles.actionRow}>
          <Pressable
            style={({ pressed }) => [
              styles.secondaryButton,
              pressed && styles.pressedButton,
            ]}
            onPress={() => loadReport(inputCode, false, strategy)}
            disabled={runLoading}
          >
            <Ionicons name="server-outline" size={18} color={marketColors.text} />
            <Text style={styles.secondaryButtonText}>讀取資料庫</Text>
          </Pressable>

          <Pressable
            style={({ pressed }) => [
              styles.searchButton,
              !selectedStrategy?.runnable && styles.searchButtonStoredOnly,
              pressed && styles.pressedButton,
              runLoading && styles.disabledButton,
            ]}
            onPress={runBacktest}
            disabled={runLoading}
          >
            <Ionicons
              name={selectedStrategy?.runnable ? "play" : "database-outline"}
              size={18}
              color={marketColors.white}
            />
            <Text style={styles.searchButtonText}>
              {runLoading
                ? "回測中…"
                : selectedStrategy?.runnable
                ? "執行回測"
                : "讀取既有結果"}
            </Text>
          </Pressable>
        </View>
      </View>

      {message ? (
        <View
          style={[
            styles.messageBox,
            messageType === "success"
              ? styles.successBox
              : messageType === "error"
              ? styles.errorBox
              : styles.infoBox,
          ]}
        >
          <Ionicons
            name={
              messageType === "success"
                ? "checkmark-circle"
                : messageType === "error"
                ? "alert-circle"
                : "information-circle"
            }
            size={21}
            color={
              messageType === "success"
                ? marketColors.success
                : messageType === "error"
                ? marketColors.danger
                : marketColors.primary
            }
          />
          <Text style={styles.messageText}>{message}</Text>
        </View>
      ) : null}

      {loading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={marketColors.primary} />
          <Text style={styles.loadingText}>{loadingText}</Text>
        </View>
      ) : report ? (
        <>
          <View style={styles.reportHero}>
            <View style={styles.reportHeroTop}>
              <View>
                <Text style={styles.stockCode}>{stockTitle}</Text>
                <Text style={styles.strategyText}>
                  {report.strategyLabel || report.strategy} 策略回測
                </Text>
              </View>
              <View style={styles.reportBadge}>
                <Text style={styles.reportBadgeText}>MySQL 已連線</Text>
              </View>
            </View>

            <Text style={styles.periodText}>
              {report.startDate || "—"} ～ {report.endDate || "—"}
            </Text>

            <View style={styles.heroResultRow}>
              <Text style={styles.heroResultLabel}>累計淨損益</Text>
              <Text
                style={[
                  styles.heroResultValue,
                  summary.totalNetPnl >= 0
                    ? styles.heroPositive
                    : styles.heroNegative,
                ]}
              >
                {formatMoney(summary.totalNetPnl)} 元
              </Text>
            </View>
          </View>

          <SectionHeader icon="time-outline" title="歷史報告" />
          {!!historyError && <Text style={styles.emptyText}>{historyError}</Text>}
          <ScrollView horizontal showsHorizontalScrollIndicator style={{ marginBottom: 16 }}>
            {history.map(item => <Pressable key={item.analysisId}
              accessibilityRole="button" accessibilityState={{ selected: item.analysisId === report.analysisId }}
              onPress={() => selectHistory(item.analysisId)} disabled={runLoading}
              style={[styles.historyCard, item.analysisId === report.analysisId && styles.strategyButtonActive]}>
              <Text style={styles.historyTitle}>{String(item.generatedAt || "").replace("T", " ").slice(0, 19)}</Text>
              <Text style={styles.historyDetail}>{item.isTestReport ? "測試報告" : item.sourceOwner === "imported" ? "匯入報告" : "我的回測"} · {item.hasHtml ? "含 HTML" : "績效摘要"}</Text>
            </Pressable>)}
          </ScrollView>
          <View style={styles.reportTabs}>
            {[{ key: "summary", label: "績效摘要" }, { key: "html", label: "原始 HTML 報告" }].map(tab =>
              <Pressable key={tab.key} accessibilityRole="button" accessibilityState={{ selected: viewMode === tab.key }}
                disabled={tab.key === "html" && !canShowHtmlReport}
                onPress={() => {
                  if (tab.key === "html" && !canShowHtmlReport) {
                    return;
                  }
                  setViewMode(tab.key);
                }}
                style={[
                  styles.reportTab,
                  viewMode === tab.key && styles.strategyButtonActive,
                  tab.key === "html" && !canShowHtmlReport && styles.reportTabDisabled,
                ]}>
                <Text style={styles.historyTitle}>{tab.label}</Text>
              </Pressable>)}
          </View>
          {viewMode === "html" ? <HtmlReportPanel key={report.analysisId} analysisId={report.analysisId}
            title={`${stockTitle} · ${report.strategyLabel || report.strategy}`} available={report.hasHtml} /> : <>
          <SectionHeader icon="speedometer-outline" title="績效摘要" />
          <View style={styles.metricsGrid}>
            <MetricCard
              icon="swap-horizontal-outline"
              label="交易次數"
              value={`${summary.totalTrades} 筆`}
            />
            <MetricCard
              icon="trophy-outline"
              label="勝率"
              value={`${formatNumber(summary.winRate)}%`}
              tone={summary.winRate >= 50 ? "positive" : "negative"}
            />
            <MetricCard
              icon="trending-up-outline"
              label="總報酬率"
              value={`${formatNumber(
                summary.totalReturnPct ?? summary.compoundedReturnPct
              )}%`}
              tone={resultTone}
            />
            <MetricCard
              icon="analytics-outline"
              label="獲利因子"
              value={
                summary.profitFactor === null
                  ? "—"
                  : formatNumber(summary.profitFactor, 3)
              }
              tone={summary.profitFactor >= 1 ? "positive" : "negative"}
            />
            <MetricCard
              icon="arrow-up-circle-outline"
              label="最佳單筆"
              value={`${formatNumber(summary.bestTradePct, 3)}%`}
              tone="positive"
            />
            <MetricCard
              icon="arrow-down-circle-outline"
              label="最差單筆"
              value={`${formatNumber(summary.worstTradePct, 3)}%`}
              tone="negative"
            />
          </View>

          <SectionHeader icon="cash-outline" title="交易成本與風險" />
          <View style={styles.infoCard}>
            <View style={styles.infoRow}>
              <Text style={styles.infoLabel}>總手續費</Text>
              <Text style={styles.infoValue}>
                {formatNumber(summary.totalFee)} 元
              </Text>
            </View>
            <View style={styles.divider} />
            <View style={styles.infoRow}>
              <Text style={styles.infoLabel}>總交易稅</Text>
              <Text style={styles.infoValue}>
                {formatNumber(summary.totalTax)} 元
              </Text>
            </View>
            <View style={styles.divider} />
            <View style={styles.infoRow}>
              <Text style={styles.infoLabel}>最大回撤</Text>
              <Text style={[styles.infoValue, styles.negativeText]}>
                {formatNumber(summary.maxDrawdownPct)}%
              </Text>
            </View>
            <View style={styles.divider} />
            <View style={styles.infoRow}>
              <Text style={styles.infoLabel}>平均單筆報酬</Text>
              <Text
                style={[
                  styles.infoValue,
                  summary.averageReturnPct >= 0
                    ? styles.positiveText
                    : styles.negativeText,
                ]}
              >
                {formatNumber(summary.averageReturnPct, 3)}%
              </Text>
            </View>
          </View>

          <SectionHeader icon="pulse-outline" title="最新技術訊號" />
          {latestSignal ? (
            <View style={styles.signalCard}>
              <View style={styles.signalHeader}>
                <View
                  style={[
                    styles.signalPill,
                    latestSignal.action === "Buy"
                      ? styles.buyPill
                      : styles.sellPill,
                  ]}
                >
                  <Text
                    style={[
                      styles.signalPillText,
                      latestSignal.action === "Buy"
                        ? styles.buyText
                        : styles.sellText,
                    ]}
                  >
                    {latestSignal.action === "Buy" ? "買進" : "賣出"}
                  </Text>
                </View>
                <Text style={styles.signalDate}>{latestSignal.datetime}</Text>
              </View>

              <Text style={styles.signalPrice}>
                訊號價格：{formatNumber(latestSignal.price, 2)}
              </Text>
              <Text style={styles.signalReason}>{latestSignal.reason}</Text>

              <View style={styles.indicatorGrid}>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>MA5</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.maFast, 2)}
                  </Text>
                </View>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>MA10</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.maMid, 2)}
                  </Text>
                </View>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>MA20</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.maSlow, 2)}
                  </Text>
                </View>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>RSI</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.rsi, 2)}
                  </Text>
                </View>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>MACD</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.macd, 3)}
                  </Text>
                </View>
                <View style={styles.indicatorItem}>
                  <Text style={styles.indicatorLabel}>柱狀圖</Text>
                  <Text style={styles.indicatorValue}>
                    {formatNumber(latestSignal.macdHist, 3)}
                  </Text>
                </View>
              </View>
            </View>
          ) : (
            <EmptyState message="這份報告沒有技術訊號資料" />
          )}

          <SectionHeader icon="receipt-outline" title="最近完成交易" />
          {report.recentTrades?.length ? (
            <View style={styles.tradesCard}>
              {report.recentTrades.slice(0, 6).map((trade, index) => (
                <View key={`${trade.sellDatetime}-${index}`}>
                  <View style={styles.tradeRow}>
                    <View style={styles.tradeMain}>
                      <Text style={styles.tradeDate}>{trade.sellDatetime}</Text>
                      <Text style={styles.tradePrices}>
                        {formatNumber(trade.buyPrice, 2)} → {formatNumber(trade.sellPrice, 2)}
                      </Text>
                    </View>
                    <View style={styles.tradeResult}>
                      <Text
                        style={[
                          styles.tradePnl,
                          trade.netPnl >= 0
                            ? styles.positiveText
                            : styles.negativeText,
                        ]}
                      >
                        {formatMoney(trade.netPnl)} 元
                      </Text>
                      <Text
                        style={[
                          styles.tradeReturn,
                          trade.returnPct >= 0
                            ? styles.positiveText
                            : styles.negativeText,
                        ]}
                      >
                        {trade.returnPct >= 0 ? "+" : ""}
                        {formatNumber(trade.returnPct, 3)}%
                      </Text>
                    </View>
                  </View>
                  {index < Math.min(report.recentTrades.length, 6) - 1 ? (
                    <View style={styles.divider} />
                  ) : null}
                </View>
              ))}
            </View>
          ) : (
            <EmptyState message="這份報告沒有交易紀錄" />
          )}

          </>}

          <Text style={styles.reportIdText}>
            分析編號：{report.analysisId ?? "—"}　資料庫報告編號：{report.reportDbId ?? "—"}
          </Text>
        </>
      ) : (
        <EmptyState message="目前沒有可顯示的回測報告" />
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  historyCard: { minWidth: 215, padding: 14, marginRight: 10, borderRadius: 12,
    backgroundColor: marketColors.surface, borderWidth: 1, borderColor: marketColors.border },
  historyTitle: { color: marketColors.text, fontSize: 14, fontWeight: "800" },
  historyDetail: { color: marketColors.textMuted, fontSize: 12, marginTop: 6 },
  reportTabs: { flexDirection: "row", gap: 10, marginBottom: 16 },
  reportTab: { flex: 1, padding: 15, alignItems: "center", borderRadius: 12,
    backgroundColor: marketColors.surface, borderWidth: 1, borderColor: marketColors.border },
  reportTabDisabled: {
    opacity: 0.45,
  },
  container: {
    flex: 1,
    backgroundColor: marketColors.background,
  },
  content: {
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
    paddingHorizontal: 12,
    paddingTop: 14,
    paddingBottom: 120,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: marketColors.header,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    borderRadius: 21,
    padding: 17,
    marginBottom: 14,
  },
  logoBox: {
    width: 58,
    height: 58,
    borderRadius: 18,
    backgroundColor: marketColors.headerAccent,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 13,
  },
  headerTextBox: { flex: 1 },
  title: {
    color: marketColors.text,
    fontSize: 25,
    fontWeight: "900",
  },
  subtitle: {
    color: "#b7cfdb",
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4,
  },
  searchCard: {
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 19,
    padding: 15,
    marginBottom: 14,
  },
  searchLabel: {
    color: "#c7d5df",
    fontWeight: "800",
    fontSize: 14,
    marginBottom: 9,
  },
  strategyRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginBottom: 10,
  },
  strategyButton: {
    borderWidth: 1,
    borderColor: marketColors.border,
    backgroundColor: marketColors.input,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 9,
    marginRight: 8,
    marginBottom: 8,
  },
  strategyButtonActive: {
    backgroundColor: marketColors.primary,
    borderColor: marketColors.primary,
  },
  strategyButtonText: {
    color: marketColors.textMuted,
    fontSize: 14,
    fontWeight: "800",
  },
  strategyButtonTextActive: {
    color: marketColors.white,
  },
  strategyHintBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    backgroundColor: "#2b2514",
    borderWidth: 1,
    borderColor: "#6b5718",
    borderRadius: 12,
    padding: 10,
    marginBottom: 12,
  },
  strategyHint: {
    flex: 1,
    color: "#f8d66d",
    fontSize: 12,
    lineHeight: 18,
    marginLeft: 7,
  },
  actionRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 11,
  },
  secondaryButton: {
    flex: 1,
    height: 50,
    borderWidth: 1,
    borderColor: marketColors.border,
    backgroundColor: marketColors.surfaceRaised,
    borderRadius: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 10,
  },
  secondaryButtonText: {
    color: marketColors.text,
    fontSize: 15,
    fontWeight: "900",
    marginLeft: 7,
  },
  searchRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  inputBox: {
    flex: 1,
    height: 54,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 14,
    backgroundColor: marketColors.input,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
  },
  input: {
    flex: 1,
    color: marketColors.text,
    fontSize: 17,
    marginLeft: 9,
  },
  searchButton: {
    flex: 1,
    height: 50,
    minWidth: 82,
    borderRadius: 14,
    backgroundColor: marketColors.primary,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 18,
  },
  searchButtonStoredOnly: {
    backgroundColor: marketColors.headerAccent,
  },
  disabledButton: { opacity: 0.55 },
  searchButtonText: {
    color: marketColors.white,
    fontSize: 15,
    fontWeight: "900",
    marginLeft: 7,
  },
  pressedButton: { opacity: 0.78 },
  messageBox: {
    borderWidth: 1,
    borderRadius: 14,
    padding: 13,
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 14,
  },
  successBox: {
    backgroundColor: marketColors.successSurface,
    borderColor: "#166534",
  },
  errorBox: {
    backgroundColor: marketColors.dangerSurface,
    borderColor: "#7f1d1d",
  },
  infoBox: {
    backgroundColor: "#0b2740",
    borderColor: marketColors.headerAccent,
  },
  messageText: {
    flex: 1,
    color: marketColors.text,
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 20,
    marginLeft: 8,
  },
  loadingBox: {
    minHeight: 260,
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  loadingText: {
    color: marketColors.textMuted,
    fontSize: 15,
    marginTop: 14,
  },
  reportHero: {
    backgroundColor: marketColors.header,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    borderRadius: 22,
    padding: 20,
    marginBottom: 20,
  },
  reportHeroTop: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
  },
  stockCode: {
    color: marketColors.white,
    fontSize: 34,
    fontWeight: "900",
  },
  strategyText: {
    color: "#b9d9ee",
    fontSize: 16,
    fontWeight: "800",
    marginTop: 3,
  },
  reportBadge: {
    backgroundColor: marketColors.headerAccent,
    borderWidth: 1,
    borderColor: "#17688f",
    borderRadius: 999,
    paddingHorizontal: 11,
    paddingVertical: 7,
  },
  reportBadgeText: {
    color: "#d8f1ff",
    fontSize: 12,
    fontWeight: "900",
  },
  periodText: {
    color: marketColors.textMuted,
    fontSize: 14,
    marginTop: 14,
  },
  heroResultRow: { marginTop: 20 },
  heroResultLabel: { color: "#b9d9ee", fontSize: 14 },
  heroResultValue: {
    fontSize: 30,
    fontWeight: "900",
    marginTop: 4,
  },
  heroPositive: { color: marketColors.rising },
  heroNegative: { color: marketColors.falling },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 2,
    marginBottom: 10,
  },
  sectionTitle: {
    color: marketColors.text,
    fontSize: 20,
    fontWeight: "900",
    marginLeft: 8,
  },
  metricsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    marginBottom: 18,
  },
  metricCard: {
    width: "48.5%",
    minHeight: 122,
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 17,
    padding: 15,
    marginBottom: 10,
  },
  metricLabel: {
    color: marketColors.textMuted,
    fontSize: 14,
    marginTop: 9,
  },
  metricValue: {
    color: marketColors.text,
    fontSize: 21,
    fontWeight: "900",
    marginTop: 5,
  },
  positiveText: { color: marketColors.rising },
  negativeText: { color: marketColors.falling },
  infoCard: {
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 19,
    paddingHorizontal: 16,
    paddingVertical: 7,
    marginBottom: 19,
  },
  infoRow: {
    minHeight: 58,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  infoLabel: { color: marketColors.textMuted, fontSize: 15 },
  infoValue: {
    color: marketColors.text,
    fontSize: 17,
    fontWeight: "900",
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: marketColors.borderSoft,
  },
  signalCard: {
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 19,
    padding: 16,
    marginBottom: 19,
  },
  signalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  signalPill: {
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 7,
  },
  buyPill: { backgroundColor: "#4a1719" },
  sellPill: { backgroundColor: "#123b23" },
  signalPillText: { fontSize: 14, fontWeight: "900" },
  buyText: { color: marketColors.rising },
  sellText: { color: marketColors.falling },
  signalDate: { color: marketColors.textSubtle, fontSize: 13 },
  signalPrice: {
    color: marketColors.text,
    fontSize: 21,
    fontWeight: "900",
    marginTop: 15,
  },
  signalReason: {
    color: marketColors.textMuted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 6,
  },
  indicatorGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    marginTop: 14,
  },
  indicatorItem: {
    width: "31.5%",
    backgroundColor: marketColors.surfaceRaised,
    borderWidth: 1,
    borderColor: marketColors.borderSoft,
    borderRadius: 13,
    padding: 11,
    marginBottom: 8,
  },
  indicatorLabel: { color: marketColors.textSubtle, fontSize: 12 },
  indicatorValue: {
    color: marketColors.text,
    fontSize: 15,
    fontWeight: "900",
    marginTop: 4,
  },
  tradesCard: {
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 19,
    paddingHorizontal: 16,
    paddingVertical: 6,
    marginBottom: 19,
  },
  tradeRow: {
    minHeight: 78,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  tradeMain: { flex: 1, paddingRight: 12 },
  tradeDate: {
    color: marketColors.text,
    fontSize: 14,
    fontWeight: "900",
  },
  tradePrices: {
    color: marketColors.textMuted,
    fontSize: 13,
    marginTop: 5,
  },
  tradeResult: { alignItems: "flex-end" },
  tradePnl: { fontSize: 16, fontWeight: "900" },
  tradeReturn: { fontSize: 13, fontWeight: "800", marginTop: 4 },
  fullReportButton: {
    height: 60,
    backgroundColor: marketColors.primary,
    borderRadius: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
  },
  fullReportButtonText: {
    color: marketColors.white,
    fontSize: 18,
    fontWeight: "900",
    marginLeft: 8,
  },
  databaseStoredBox: {
    minHeight: 56,
    backgroundColor: marketColors.successSurface,
    borderWidth: 1,
    borderColor: "#166534",
    borderRadius: 14,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 11,
    marginTop: 2,
  },
  databaseStoredText: {
    flex: 1,
    color: "#bbf7d0",
    fontSize: 13,
    lineHeight: 19,
    fontWeight: "700",
    marginLeft: 8,
  },
  reportIdText: {
    color: marketColors.textSubtle,
    fontSize: 11,
    textAlign: "center",
    marginTop: 14,
  },
  emptyCard: {
    minHeight: 190,
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
    padding: 22,
  },
  emptyText: {
    color: marketColors.textMuted,
    fontSize: 15,
    textAlign: "center",
    marginTop: 12,
  },
});
