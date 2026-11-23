import usePageLayout from '../styles/usePageLayout';
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  Pressable,
  RefreshControl,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, router } from "expo-router";
import MarketHeader from "../components/MarketHeader";
import MarketStockRow from "../components/MarketStockRow";
import MarketTrendChart from "../components/MarketTrendChart";
import { apiRequest } from "../src/config/api";
import { colors as marketColors, radius, shadow, space, type, ui } from "../styles/theme";

function formatPrice(value) {
  if (!Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function formatPercent(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function MarketIndexCard({ item }) {
  const change = Number(item?.change);
  const rising = Number.isFinite(change) ? change >= 0 : true;
  const color = rising ? marketColors.rising : marketColors.falling;

  return (
    <View style={styles.indexCard}>
      <View style={styles.indexTitleRow}>
        <Text style={styles.indexName}>{item?.name}</Text>
        <Text style={styles.indexSource}>Yahoo</Text>
      </View>
      <View style={styles.indexPriceRow}>
        <Text style={[styles.indexPrice, { color }]}>{formatPrice(item?.price)}</Text>
        <Text style={[styles.indexPercent, { color }]}>
          {formatPercent(item?.changePercent)}
        </Text>
      </View>
      <MarketTrendChart stock={item} height={320} />
    </View>
  );
}

export default function HomeScreen() {
 const layout=usePageLayout();
  const [query, setQuery] = useState("");
  const [stocks, setStocks] = useState([]);
  const [indices, setIndices] = useState([]);
  const [title, setTitle] = useState("Yahoo 熱門成交");
  const [updatedAt, setUpdatedAt] = useState(null);
  const [sourceNote, setSourceNote] = useState("Yahoo Finance 行情可能為延遲資料");
  const [favoriteCodes, setFavoriteCodes] = useState(() => new Set());
  const [favoriteLoadingCodes, setFavoriteLoadingCodes] = useState(() => new Set());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(null);

  const updateTimeText = useMemo(() => {
    if (!updatedAt) return "尚未更新";
    const date = new Date(updatedAt);
    if (Number.isNaN(date.getTime())) return "剛剛更新";
    return `更新 ${date.toLocaleTimeString("zh-TW", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`;
  }, [updatedAt]);

  const applyResponse = useCallback((data) => {
    setStocks(Array.isArray(data?.stocks) ? data.stocks : []);
    setIndices(Array.isArray(data?.indices) ? data.indices : []);
    setTitle(data?.title || "Yahoo 熱門成交");
    setUpdatedAt(data?.updatedAt || new Date().toISOString());
    setSourceNote(data?.sourceNote || "Yahoo Finance 行情可能為延遲資料");
  }, []);

  const loadFavoriteCodes = useCallback(async () => {
    try {
      const data = await apiRequest("/api/favorites/codes", { timeoutMs: 15_000 });
      setFavoriteCodes(new Set(Array.isArray(data?.codes) ? data.codes.map(String) : []));
    } catch (requestError) {
      if (requestError?.status === 401) {
        setNotice({ type: "error", text: "登入已過期，請重新登入後使用自選股" });
      } else {
        console.warn("讀取自選股狀態失敗：", requestError.message);
      }
    }
  }, []);

  const loadHotStocks = useCallback(
    async ({ silent = false } = {}) => {
      if (!silent) setLoading(true);
      setError("");

      try {
        const [data] = await Promise.all([
          apiRequest("/api/market/hot", { timeoutMs: 35_000 }),
          loadFavoriteCodes(),
        ]);
        applyResponse(data);
      } catch (requestError) {
        setError(requestError.message || "讀取 Yahoo 熱門股票失敗");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [applyResponse, loadFavoriteCodes]
  );

  const handleSearch = useCallback(async () => {
    const keyword = query.trim();
    Keyboard.dismiss();

    if (!keyword) {
      await loadHotStocks();
      return;
    }

    setSearching(true);
    setError("");

    try {
      const [data] = await Promise.all([
        apiRequest(`/api/market/search?q=${encodeURIComponent(keyword)}`, {
          timeoutMs: 35_000,
        }),
        loadFavoriteCodes(),
      ]);
      applyResponse(data);
    } catch (requestError) {
      setError(requestError.message || "Yahoo 股票搜尋失敗");
    } finally {
      setSearching(false);
    }
  }, [applyResponse, loadFavoriteCodes, loadHotStocks, query]);

  const handleToggleFavorite = useCallback(
    async (stock) => {
      const code = String(stock?.code || "").trim();
      if (!code || favoriteLoadingCodes.has(code)) return;

      const removing = favoriteCodes.has(code);
      setFavoriteLoadingCodes((previous) => {
        const next = new Set(previous);
        next.add(code);
        return next;
      });
      setNotice(null);

      try {
        const data = removing
          ? await apiRequest(`/api/favorites/${encodeURIComponent(code)}`, {
              method: "DELETE",
              timeoutMs: 20_000,
            })
          : await apiRequest("/api/favorites", {
              method: "POST",
              body: JSON.stringify({
                code,
                yahooSymbol: stock?.yahooSymbol,
              }),
              timeoutMs: 25_000,
            });

        setFavoriteCodes((previous) => {
          const next = new Set(previous);
          if (removing) next.delete(code);
          else next.add(code);
          return next;
        });
        setNotice({ type: "success", text: data?.message || "自選股已更新" });
      } catch (requestError) {
        setNotice({
          type: "error",
          text: requestError.message || "更新自選股失敗",
        });
      } finally {
        setFavoriteLoadingCodes((previous) => {
          const next = new Set(previous);
          next.delete(code);
          return next;
        });
      }
    },
    [favoriteCodes, favoriteLoadingCodes]
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    if (query.trim()) await handleSearch();
    else await loadHotStocks({ silent: true });
    setRefreshing(false);
  }, [handleSearch, loadHotStocks, query]);

  const clearSearch = useCallback(() => {
    setQuery("");
    loadHotStocks();
  }, [loadHotStocks]);

  useFocusEffect(
    useCallback(() => {
      loadFavoriteCodes();
    }, [loadFavoriteCodes])
  );

  useEffect(() => {
    loadHotStocks();
    const timer = setInterval(() => {
      if (!query.trim()) loadHotStocks({ silent: true });
    }, 30_000);
    return () => clearInterval(timer);
  }, [loadHotStocks, query]);

  const header = (
    <View>
      <Pressable accessibilityRole="button" onPress={()=>router.push('/analysis')} style={({pressed})=>[styles.labCard,pressed&&{backgroundColor:marketColors.surfacePressed}]}><Text style={ui.eyebrow}>STRATEGY LAB</Text><Text style={styles.labTitle}>這個策略，會在哪裡買進與賣出？</Text><Text style={ui.muted}>選擇股票與歷史期間，查看交易回放、比較策略表現，再閱讀 Gemini 重點分析。</Text><View style={styles.labButton}><Text style={ui.buttonPrimaryText}>開始策略模擬 →</Text></View></Pressable>
      <MarketHeader
        title="台股即時行情"
        subtitle="Yahoo Finance 行情・最近一個月走勢・日 K"
        icon="pulse"
      >
        <View style={styles.searchRow}>
          <Ionicons name="search" size={19} color={marketColors.textMuted} />
          <TextInput
            style={styles.searchInput}
            value={query}
            onChangeText={setQuery}
            placeholder="輸入股票代號或名稱，例如 2330、台積電"
            placeholderTextColor={marketColors.textSubtle}
            returnKeyType="search"
            autoCapitalize="none"
            autoCorrect={false}
            onSubmitEditing={handleSearch}
            editable={!searching}
          />
          {query ? (
            <Pressable onPress={clearSearch} style={styles.clearButton}>
              <Ionicons name="close-circle" size={21} color={marketColors.textMuted} />
            </Pressable>
          ) : null}
          <Pressable
            onPress={handleSearch}
            disabled={searching}
            style={({ pressed }) => [
              styles.searchButton,
              pressed && styles.searchButtonPressed,
              searching && styles.searchButtonDisabled,
            ]}
          >
            {searching ? (
              <ActivityIndicator color={marketColors.white} />
            ) : (
              <Text style={styles.searchButtonText}>搜尋</Text>
            )}
          </Pressable>
        </View>
      </MarketHeader>

      {indices.length > 0 ? (
        <View style={[styles.indicesRow,layout.compact&&{flexDirection:"column"}]}>
          {indices.slice(0, 2).map((item) => (
            <MarketIndexCard key={item.yahooSymbol || item.code} item={item} />
          ))}
        </View>
      ) : null}

      <View style={styles.sectionRow}>
        <View style={styles.sectionTitleBox}>
          <Ionicons name="flame" size={19} color={marketColors.warning} />
          <Text numberOfLines={1} style={styles.sectionTitle}>
            {title}
          </Text>
        </View>
        <Text style={styles.updateText}>{updateTimeText}</Text>
      </View>

      <View style={styles.infoBox}>
        <Ionicons name="information-circle-outline" size={16} color={marketColors.info} />
        <Text style={styles.infoText}>資料來源：Yahoo Finance。{sourceNote}</Text>
      </View>

      {notice ? (
        <View style={[styles.noticeBox, notice.type === "success" ? styles.successBox : styles.errorBox]}>
          <Ionicons
            name={notice.type === "success" ? "checkmark-circle" : "alert-circle"}
            size={19}
            color={notice.type === "success" ? marketColors.success : marketColors.danger}
          />
          <Text
            style={[
              styles.noticeText,
              { color: notice.type === "success" ? marketColors.successText : marketColors.dangerText },
            ]}
          >
            {notice.text}
          </Text>
          <Pressable onPress={() => setNotice(null)}>
            <Ionicons name="close" size={18} color={marketColors.textMuted} />
          </Pressable>
        </View>
      ) : null}

      {error ? (
        <View style={[styles.noticeBox, styles.errorBox]}>
          <Ionicons name="alert-circle" size={19} color={marketColors.danger} />
          <Text style={[styles.noticeText, { color: marketColors.dangerText }]}>{error}</Text>
          <Pressable onPress={() => loadHotStocks()}>
            <Text style={styles.retryText}>重試</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );

  const emptyContent = loading ? (
    <View style={styles.stateCard}>
      <ActivityIndicator size="large" color={marketColors.primary} />
      <Text style={styles.stateText}>正在從 Yahoo Finance 載入行情…</Text>
    </View>
  ) : (
    <View style={styles.stateCard}>
      <Ionicons name="search-outline" size={44} color={marketColors.textSubtle} />
      <Text style={styles.emptyTitle}>Yahoo Finance 找不到符合的股票</Text>
      <Text style={styles.emptySubtitle}>請改用完整股票代號或公司名稱搜尋</Text>
    </View>
  );

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor={marketColors.background} />
      <FlatList
        style={styles.list}
        data={stocks}
        keyExtractor={(item) => item.yahooSymbol || `${item.market}-${item.code}`}
        renderItem={({ item }) => (
          <MarketStockRow
            stock={item}
            isFavorite={favoriteCodes.has(String(item.code))}
            favoriteLoading={favoriteLoadingCodes.has(String(item.code))}
            onToggleFavorite={handleToggleFavorite}
          />
        )}
        ListHeaderComponent={header}
        ListEmptyComponent={emptyContent}
        contentContainerStyle={[styles.listContent,layout.page]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor={marketColors.white}
            colors={[marketColors.primary]}
          />
        }
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: marketColors.background },
  list: { width: "100%", alignSelf: "center" },
  listContent: { paddingTop: space.lg, paddingBottom: 120 },
  labCard: {
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: radius.xl,
    padding: space.xl,
    marginBottom: space.md,
    gap: space.xs,
    ...shadow,
  },
  labTitle: { ...type.title, fontSize: 24, lineHeight: 32, fontWeight: "800" },
  labButton: { ...StyleSheet.flatten(ui.buttonPrimary), alignSelf: "flex-start", marginTop: space.xs },
  searchRow: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: marketColors.input,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: radius.md,
    paddingLeft: space.sm,
    overflow: "hidden",
  },
  searchInput: {
    flex: 1,
    minWidth: 0,
    minHeight: 46,
    paddingHorizontal: space.xs,
    color: marketColors.text,
    fontSize: 15,
    fontFamily: type.body.fontFamily,
    outlineStyle: "none",
  },
  clearButton: { width: 40, height: 46, alignItems: "center", justifyContent: "center" },
  searchButton: {
    width: 76,
    alignSelf: "stretch",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.primary,
  },
  searchButtonPressed: { backgroundColor: marketColors.primaryPressed },
  searchButtonDisabled: { opacity: 0.6 },
  searchButtonText: { ...type.body, color: marketColors.white, fontWeight: "700" },
  indicesRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.sm,
    marginBottom: space.lg,
  },
  indexCard: {
    flex: 1,
    minWidth: 260,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: radius.lg,
    backgroundColor: marketColors.surface,
    padding: space.md,
    gap: space.xs,
    ...shadow,
  },
  indexTitleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  indexName: { ...type.subheading, fontWeight: "700" },
  indexSource: { ...type.caption },
  indexPriceRow: { flexDirection: "row", alignItems: "flex-end", gap: space.xs, marginBottom: space.xxs },
  indexPrice: { ...type.number, fontSize: 26, lineHeight: 32, fontWeight: "700" },
  indexPercent: { ...type.number, fontSize: 13, fontWeight: "700", marginBottom: 4 },
  sectionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: space.xs,
    marginBottom: space.sm,
  },
  sectionTitleBox: { flexDirection: "row", alignItems: "center", gap: space.xs, flex: 1 },
  sectionTitle: { ...type.title, flex: 1 },
  updateText: { ...type.caption, ...type.number, marginLeft: space.xs },
  infoBox: { ...StyleSheet.flatten([ui.notice, ui.noticeInfo]), alignItems: "center", marginBottom: space.sm },
  infoText: { ...StyleSheet.flatten(ui.noticeInfoText) },
  noticeBox: { ...StyleSheet.flatten(ui.notice), alignItems: "center", marginBottom: space.sm },
  successBox: StyleSheet.flatten(ui.noticeSuccess),
  errorBox: StyleSheet.flatten(ui.noticeError),
  noticeText: { ...type.small, flex: 1 },
  retryText: { ...type.small, color: marketColors.primaryText, fontWeight: "700" },
  stateCard: {
    minHeight: 260,
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs,
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: radius.lg,
    paddingHorizontal: space.xl,
  },
  stateText: { ...type.small, marginTop: space.xs },
  emptyTitle: { ...type.heading, marginTop: space.xs, textAlign: "center" },
  emptySubtitle: { ...type.small, textAlign: "center" },
});
