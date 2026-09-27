import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import MarketTrendChart from "./MarketTrendChart";
import { marketColors } from "../styles/marketTheme";

function formatPrice(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return number >= 1000
    ? number.toLocaleString("zh-TW", { maximumFractionDigits: 2 })
    : number.toLocaleString("zh-TW", {
        minimumFractionDigits: number < 100 ? 2 : 1,
        maximumFractionDigits: 2,
      });
}

function formatChange(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function formatPercent(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatVolume(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (number >= 100_000_000) return `${(number / 100_000_000).toFixed(1)} 億股`;
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)} 百萬股`;
  if (number >= 1_000) return `${Math.round(number / 1000).toLocaleString("zh-TW")} 千股`;
  return `${Math.round(number).toLocaleString("zh-TW")} 股`;
}

function marketLabel(stock) {
  if (stock?.market === "otc") return "上櫃";
  if (stock?.market === "index") return "指數";
  return "上市";
}

function displayStockName(stock) {
  const name = String(stock?.name || "").trim();
  if (name) return name;
  return String(stock?.yahooSymbol || stock?.code || "未知股票");
}

export default function MarketStockRow({
  stock,
  isFavorite = false,
  favoriteLoading = false,
  onToggleFavorite,
  style,
}) {
  const { width } = useWindowDimensions();
  const compact = width < 520;
  const change = Number(stock?.change);
  const rising = Number.isFinite(change) ? change >= 0 : true;
  const trendColor = rising ? marketColors.rising : marketColors.falling;
  const historyValues = Array.isArray(stock?.history) ? stock.history : [];
  const available = stock?.available !== false && Number.isFinite(Number(stock?.price));

  return (
    <View style={[styles.card, compact ? styles.compactCard : styles.wideCard, compact && { flexDirection: "column" }, style]}>
      <View style={[styles.quotePanel, compact ? styles.compactQuotePanel : styles.wideQuotePanel]}>
        <View style={styles.nameRow}>
          <View style={styles.nameBox}>
            <Text
              numberOfLines={2}
              style={[styles.stockName, compact ? styles.compactName : styles.wideName]}
            >
              {displayStockName(stock)}
            </Text>
            <View style={styles.metaRow}>
              <View style={styles.marketPill}>
                <Text style={styles.marketPillText}>
                  {marketLabel(stock)} {stock?.code}
                </Text>
              </View>
              <Text numberOfLines={1} style={styles.sourceText}>
                Yahoo Finance
              </Text>
            </View>
          </View>

          {onToggleFavorite ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={isFavorite ? "移除自選股" : "加入自選股"}
              onPress={() => onToggleFavorite(stock)}
              disabled={favoriteLoading}
              style={({ pressed }) => [
                styles.favoriteButton,
                isFavorite ? styles.favoriteButtonActive : styles.favoriteButtonIdle,
                (pressed || favoriteLoading) && styles.buttonPressed,
              ]}
            >
              {favoriteLoading ? (
                <ActivityIndicator size="small" color="#f4c430" />
              ) : (
                <Ionicons
                  name={isFavorite ? "star" : "star-outline"}
                  size={23}
                  color={isFavorite ? "#ffd54a" : "#b9c6d1"}
                />
              )}
            </Pressable>
          ) : null}
        </View>

        {available ? (
          <View style={styles.priceRow}>
            <Text
              style={[
                styles.price,
                compact ? styles.compactPrice : styles.widePrice,
                { color: trendColor },
              ]}
            >
              {formatPrice(stock?.price)}
            </Text>

            <View style={styles.changeBox}>
              <Text style={[styles.changeText, { color: trendColor }]}>
                {formatChange(stock?.change)}
              </Text>
              <Text style={[styles.changeText, { color: trendColor }]}>
                {formatPercent(stock?.changePercent)}
              </Text>
            </View>
          </View>
        ) : (
          <View>
            <Text style={styles.unavailableTitle}>Yahoo 行情暫時無法取得</Text>
            <Text style={styles.unavailableSubtitle}>
              可下拉重新整理，不會顯示虛構價格
            </Text>
          </View>
        )}

        <View style={styles.volumeRow}>
          <Ionicons name="bar-chart-outline" size={15} color={marketColors.textMuted} />
          <Text style={styles.volumeText}>成交量 {formatVolume(stock?.volume)}</Text>
        </View>
      </View>

      <View style={{ flex: compact ? undefined : 2, width: compact ? "100%" : undefined, padding: 12 }}><MarketTrendChart stock={stock} height={compact ? 210 : 230} /></View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: "100%",
    flexDirection: "row",
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 18,
    marginBottom: 12,
    overflow: "hidden",
  },
  compactCard: { minHeight: 142 },
  wideCard: { minHeight: 156 },
  quotePanel: {
    flex: 1,
    justifyContent: "space-between",
  },
  compactQuotePanel: {
    paddingHorizontal: 13,
    paddingVertical: 13,
  },
  wideQuotePanel: {
    paddingHorizontal: 17,
    paddingVertical: 13,
  },
  nameRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  nameBox: {
    flex: 1,
    paddingRight: 8,
  },
  stockName: {
    color: marketColors.text,
    fontWeight: "900",
  },
  compactName: { fontSize: 20, lineHeight: 25 },
  wideName: { fontSize: 23, lineHeight: 28 },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 4,
  },
  marketPill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    backgroundColor: marketColors.surfaceRaised,
  },
  marketPillText: {
    color: "#c6d4df",
    fontSize: 11,
    fontWeight: "800",
  },
  sourceText: {
    color: marketColors.textSubtle,
    fontSize: 10,
    marginLeft: 7,
    flexShrink: 1,
  },
  favoriteButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
  },
  favoriteButtonActive: {
    backgroundColor: "#423817",
    borderColor: "#f4c430",
  },
  favoriteButtonIdle: {
    backgroundColor: marketColors.surfaceRaised,
    borderColor: marketColors.border,
  },
  buttonPressed: { opacity: 0.7 },
  priceRow: {
    flexDirection: "row",
    alignItems: "flex-end",
  },
  price: {
    fontWeight: "600",
    letterSpacing: -1,
  },
  compactPrice: { fontSize: 34 },
  widePrice: { fontSize: 42 },
  changeBox: {
    marginLeft: 11,
    paddingBottom: 3,
  },
  changeText: {
    fontSize: 15,
    fontWeight: "800",
  },
  unavailableTitle: {
    color: marketColors.textMuted,
    fontSize: 17,
    fontWeight: "800",
  },
  unavailableSubtitle: {
    color: marketColors.textSubtle,
    fontSize: 12,
    marginTop: 4,
  },
  volumeRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  volumeText: {
    color: "#cbd7e0",
    marginLeft: 6,
    fontSize: 13,
  },
  chartPanel: {
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 9,
    backgroundColor: "#07131e",
    borderLeftWidth: 1,
    borderLeftColor: marketColors.borderSoft,
  },
  compactChartPanel: { width: 142 },
  wideChartPanel: { width: 205 },
  noChartBox: { alignItems: "center" },
  noChartText: {
    color: marketColors.textSubtle,
    fontSize: 11,
    marginTop: 7,
  },
  chartCaption: {
    color: marketColors.textSubtle,
    fontSize: 10,
    marginTop: 5,
  },
});
