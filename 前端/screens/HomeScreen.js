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
import { useFocusEffect } from "expo-router";
import MarketHeader from "../components/MarketHeader";
import MarketStockRow from "../components/MarketStockRow";
import Sparkline from "../components/Sparkline";
import { apiRequest } from "../src/config/api";
import { marketColors } from "../styles/marketTheme";

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
      <View style={styles.indexChart}>
        <Sparkline values={item?.history || []} width={142} height={42} color={color} />
      </View>
    </View>
  );
}

export default function HomeScreen() {
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
      <MarketHeader
        title="台股即時行情"
        subtitle="Yahoo Finance 成交價・熱門股票・當日 5 分走勢"
        icon="pulse"
      >
        <View style={styles.searchRow}>
          <Ionicons name="search" size={22} color={marketColors.textMuted} />
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
        <View style={styles.indicesRow}>
          {indices.slice(0, 2).map((item) => (
            <MarketIndexCard key={item.yahooSymbol || item.code} item={item} />
          ))}
        </View>
      ) : null}

      <View style={styles.sectionRow}>
        <View style={styles.sectionTitleBox}>
          <Ionicons name="flame" size={20} color="#ff5d3a" />
          <Text numberOfLines={1} style={styles.sectionTitle}>
            {title}
          </Text>
        </View>
        <Text style={styles.updateText}>{updateTimeText}</Text>
      </View>

      <View style={styles.infoBox}>
        <Ionicons name="information-circle-outline" size={17} color="#74b9ff" />
        <Text style={styles.infoText}>資料來源：Yahoo Finance。{sourceNote}</Text>
      </View>

      {notice ? (
        <View style={[styles.noticeBox, notice.type === "success" ? styles.successBox : styles.errorBox]}>
          <Ionicons
            name={notice.type === "success" ? "checkmark-circle" : "alert-circle"}
            size={19}
            color={notice.type === "success" ? "#4ade80" : "#f87171"}
          />
          <Text
            style={[
              styles.noticeText,
              { color: notice.type === "success" ? "#bbf7d0" : "#fecaca" },
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
          <Ionicons name="alert-circle" size={20} color="#f87171" />
          <Text style={[styles.noticeText, { color: "#fecaca" }]}>{error}</Text>
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
      <Ionicons name="search-outline" size={48} color="#526979" />
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
        contentContainerStyle={styles.listContent}
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
  safeArea: {
    flex: 1,
    backgroundColor: marketColors.background,
  },
  list: {
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
  },
  listContent: {
    paddingHorizontal: 12,
    paddingTop: 14,
    paddingBottom: 120,
  },
  searchRow: {
    minHeight: 52,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: marketColors.input,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 14,
    paddingLeft: 13,
    overflow: "hidden",
  },
  searchInput: {
    flex: 1,
    minHeight: 50,
    paddingHorizontal: 10,
    color: marketColors.text,
    fontSize: 16,
  },
  clearButton: {
    width: 42,
    height: 50,
    alignItems: "center",
    justifyContent: "center",
  },
  searchButton: {
    width: 72,
    height: 52,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.primary,
  },
  searchButtonPressed: { backgroundColor: marketColors.primaryPressed },
  searchButtonDisabled: { opacity: 0.7 },
  searchButtonText: {
    color: marketColors.white,
    fontWeight: "900",
    fontSize: 16,
  },
  indicesRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginBottom: 16,
  },
  indexCard: {
    flex: 1,
    minWidth: 150,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 17,
    backgroundColor: marketColors.surface,
    padding: 13,
  },
  indexTitleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  indexName: {
    color: marketColors.text,
    fontSize: 16,
    fontWeight: "900",
  },
  indexSource: {
    color: marketColors.textSubtle,
    fontSize: 11,
  },
  indexPriceRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    marginTop: 7,
  },
  indexPrice: {
    fontSize: 25,
    fontWeight: "700",
  },
  indexPercent: {
    fontSize: 12,
    fontWeight: "800",
    marginLeft: 7,
    marginBottom: 4,
  },
  indexChart: {
    marginTop: 8,
    alignItems: "center",
  },
  sectionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 2,
    marginBottom: 10,
  },
  sectionTitleBox: {
    flexDirection: "row",
    alignItems: "center",
    flex: 1,
  },
  sectionTitle: {
    color: marketColors.text,
    fontSize: 20,
    fontWeight: "900",
    marginLeft: 7,
    flex: 1,
  },
  updateText: {
    color: marketColors.textSubtle,
    fontSize: 11,
    marginLeft: 8,
  },
  infoBox: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 11,
    paddingHorizontal: 11,
    paddingVertical: 9,
    borderRadius: 13,
    backgroundColor: "#0b2436",
    borderWidth: 1,
    borderColor: "#17425c",
  },
  infoText: {
    color: "#b9d9ee",
    fontSize: 12,
    marginLeft: 7,
    flex: 1,
  },
  noticeBox: {
    marginBottom: 11,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 13,
  },
  successBox: {
    borderColor: "#166534",
    backgroundColor: "#0b2b20",
  },
  errorBox: {
    borderColor: "#7f1d1d",
    backgroundColor: "#331719",
  },
  noticeText: {
    flex: 1,
    marginLeft: 8,
  },
  retryText: {
    color: "#60a5fa",
    fontWeight: "800",
  },
  stateCard: {
    minHeight: 260,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 20,
    paddingHorizontal: 24,
    marginTop: 2,
  },
  stateText: {
    color: marketColors.textMuted,
    marginTop: 12,
  },
  emptyTitle: {
    color: marketColors.text,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 12,
    textAlign: "center",
  },
  emptySubtitle: {
    color: marketColors.textSubtle,
    marginTop: 6,
    textAlign: "center",
  },
});
