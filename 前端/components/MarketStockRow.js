import {router} from 'expo-router';
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
import { colors as marketColors, radius, shadow, space, type } from "../styles/theme";

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
  const compact = width < 760;
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
                <ActivityIndicator size="small" color={marketColors.star} />
              ) : (
                <Ionicons
                  name={isFavorite ? "star" : "star-outline"}
                  size={23}
                  color={isFavorite ? marketColors.star : marketColors.textMuted}
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

        <Pressable accessibilityRole="button" onPress={()=>router.push({pathname:'/analysis',params:{stock:String(stock.code)}})} style={({pressed})=>[styles.simulateButton,pressed&&styles.buttonPressed]}><Text style={styles.simulateText}>模擬這檔股票的策略 →</Text></Pressable>
        <View style={styles.volumeRow}>
          <Ionicons name="bar-chart-outline" size={15} color={marketColors.textMuted} />
          <Text style={styles.volumeText}>成交量 {formatVolume(stock?.volume)}</Text>
        </View>
      </View>

      <View style={[styles.chartWrap, compact && styles.chartWrapCompact, { flex: compact ? undefined : 2, width: compact ? "100%" : undefined }]}><MarketTrendChart stock={stock} height={compact ? 210 : 230} /></View>
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
    borderRadius: radius.lg,
    marginBottom: space.sm,
    overflow: "hidden",
    ...shadow,
  },
  compactCard: { minHeight: 142 },
  wideCard: { minHeight: 156 },
  quotePanel: {
    flex: 1,
    justifyContent: "space-between",
    gap: space.sm,
  },
  compactQuotePanel: { padding: space.md },
  wideQuotePanel: { padding: space.lg },
  nameRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: space.xs,
  },
  nameBox: { flex: 1, minWidth: 0 },
  stockName: { ...type.heading, fontWeight: "800" },
  compactName: { fontSize: 19, lineHeight: 26 },
  wideName: { fontSize: 21, lineHeight: 28 },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
    marginTop: space.xxs,
  },
  marketPill: {
    paddingHorizontal: space.xs,
    paddingVertical: 2,
    borderRadius: radius.pill,
    backgroundColor: marketColors.surfaceRaised,
    borderWidth: 1,
    borderColor: marketColors.border,
  },
  marketPillText: { ...type.caption, color: marketColors.textMuted, fontWeight: "700", ...type.number },
  sourceText: { ...type.caption, flexShrink: 1 },
  favoriteButton: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
  },
  favoriteButtonActive: {
    backgroundColor: "rgba(252,201,63,0.12)",
    borderColor: "rgba(252,201,63,0.55)",
  },
  favoriteButtonIdle: {
    backgroundColor: marketColors.surfaceRaised,
    borderColor: marketColors.border,
  },
  buttonPressed: { opacity: 0.7 },
  priceRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: space.sm,
  },
  price: { ...type.number, fontWeight: "700", letterSpacing: -0.5 },
  compactPrice: { fontSize: 32, lineHeight: 38 },
  widePrice: { fontSize: 38, lineHeight: 44 },
  changeBox: { paddingBottom: 4 },
  changeText: { ...type.number, fontSize: 14, lineHeight: 19, fontWeight: "700" },
  unavailableTitle: { ...type.subheading, color: marketColors.textMuted },
  unavailableSubtitle: { ...type.caption, marginTop: space.xxs },
  simulateButton: {
    alignSelf: "flex-start",
    paddingVertical: space.xs,
    paddingHorizontal: space.sm,
    borderRadius: radius.sm,
    backgroundColor: marketColors.primarySoft,
  },
  simulateText: { ...type.small, color: marketColors.primaryText, fontWeight: "700" },
  volumeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xxs,
  },
  volumeText: { ...type.small, ...type.number },
  chartWrap: {
    padding: space.sm,
    borderLeftWidth: 1,
    borderLeftColor: marketColors.borderSoft,
    backgroundColor: marketColors.surfaceSunken,
  },
  chartWrapCompact: {
    borderLeftWidth: 0,
    borderTopWidth: 1,
    borderTopColor: marketColors.borderSoft,
  },
});
