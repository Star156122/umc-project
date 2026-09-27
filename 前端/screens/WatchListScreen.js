import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import MarketHeader from "../components/MarketHeader";
import MarketStockRow from "../components/MarketStockRow";
import { apiRequest } from "../src/config/api";
import { marketColors } from "../styles/marketTheme";

export default function WatchListScreen() {
  const [stocks, setStocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [removingCodes, setRemovingCodes] = useState(() => new Set());
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);

  const loadFavorites = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError("");

    try {
      const data = await apiRequest("/api/favorites", { timeoutMs: 40_000 });
      setStocks(Array.isArray(data?.stocks) ? data.stocks : []);
      setUpdatedAt(data?.updatedAt || new Date().toISOString());
    } catch (requestError) {
      setError(requestError.message || "讀取自選股失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadFavorites();
    }, [loadFavorites])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadFavorites({ silent: true });
    setRefreshing(false);
  }, [loadFavorites]);

  const handleRemove = useCallback(
    async (stock) => {
      const code = String(stock?.code || "").trim();
      if (!code || removingCodes.has(code)) return;

      setRemovingCodes((previous) => {
        const next = new Set(previous);
        next.add(code);
        return next;
      });
      setError("");

      try {
        await apiRequest(`/api/favorites/${encodeURIComponent(code)}`, {
          method: "DELETE",
          timeoutMs: 20_000,
        });
        setStocks((previous) => previous.filter((item) => String(item.code) !== code));
      } catch (requestError) {
        setError(requestError.message || "移除自選股失敗");
      } finally {
        setRemovingCodes((previous) => {
          const next = new Set(previous);
          next.delete(code);
          return next;
        });
      }
    },
    [removingCodes]
  );

  const updateTimeText = (() => {
    if (!updatedAt) return "尚未更新";
    const date = new Date(updatedAt);
    if (Number.isNaN(date.getTime())) return "剛剛更新";
    return date.toLocaleTimeString("zh-TW", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  })();

  const header = (
    <View>
      <MarketHeader
        title="我的自選股"
        subtitle="收藏存入 MySQL favorite_stocks，行情由 Yahoo Finance 讀取"
        icon="star"
        rightContent={
          <View style={styles.countBadge}>
            <Text style={styles.countValue}>{stocks.length}</Text>
            <Text style={styles.countLabel}>檔股票</Text>
          </View>
        }
      />

      <View style={styles.sectionRow}>
        <View style={styles.sectionTitleBox}>
          <Ionicons name="star" size={19} color="#ffd54a" />
          <Text style={styles.sectionTitle}>自選清單</Text>
        </View>
        <Text style={styles.updateText}>更新 {updateTimeText}</Text>
      </View>

      <View style={styles.infoBox}>
        <Ionicons name="information-circle-outline" size={17} color="#74b9ff" />
        <Text style={styles.infoText}>
          金色星號代表已加入自選；再次按下即可從資料庫移除。
        </Text>
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Ionicons name="alert-circle" size={20} color="#f87171" />
          <Text style={styles.errorText}>{error}</Text>
          <Pressable onPress={() => loadFavorites()}>
            <Text style={styles.retryText}>重試</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );

  const emptyContent = loading ? (
    <View style={styles.stateCard}>
      <ActivityIndicator size="large" color={marketColors.primary} />
      <Text style={styles.stateText}>正在讀取資料庫與 Yahoo 行情…</Text>
    </View>
  ) : (
    <View style={styles.stateCard}>
      <View style={styles.emptyIconBox}>
        <Ionicons name="star-outline" size={39} color="#ffd54a" />
      </View>
      <Text style={styles.emptyTitle}>還沒有自選股</Text>
      <Text style={styles.emptySubtitle}>
        回到首頁，在股票右上角按下星號，就會存入 favorite_stocks 並顯示在這裡。
      </Text>
    </View>
  );

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor={marketColors.background} />
      <FlatList
        style={styles.list}
        data={stocks}
        keyExtractor={(item) => String(item.favoriteId || item.yahooSymbol || item.code)}
        renderItem={({ item }) => (
          <MarketStockRow
            stock={item}
            isFavorite
            favoriteLoading={removingCodes.has(String(item.code))}
            onToggleFavorite={handleRemove}
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
  countBadge: {
    minWidth: 58,
    height: 48,
    paddingHorizontal: 12,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.headerAccent,
    borderWidth: 1,
    borderColor: "#17688f",
  },
  countValue: {
    color: marketColors.white,
    fontSize: 18,
    fontWeight: "900",
  },
  countLabel: {
    color: "#b8d4e1",
    fontSize: 9,
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
  },
  sectionTitle: {
    color: marketColors.text,
    fontSize: 20,
    fontWeight: "900",
    marginLeft: 7,
  },
  updateText: {
    color: marketColors.textSubtle,
    fontSize: 11,
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
  errorBox: {
    marginBottom: 11,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 13,
    borderColor: "#7f1d1d",
    backgroundColor: "#331719",
  },
  errorText: {
    flex: 1,
    color: "#fecaca",
    marginLeft: 8,
  },
  retryText: {
    color: "#60a5fa",
    fontWeight: "800",
  },
  stateCard: {
    minHeight: 280,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 20,
    paddingHorizontal: 28,
  },
  stateText: {
    color: marketColors.textMuted,
    marginTop: 12,
  },
  emptyIconBox: {
    width: 78,
    height: 78,
    borderRadius: 39,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.surfaceRaised,
    borderWidth: 1,
    borderColor: marketColors.border,
  },
  emptyTitle: {
    color: marketColors.text,
    fontSize: 20,
    fontWeight: "900",
    marginTop: 18,
  },
  emptySubtitle: {
    color: marketColors.textSubtle,
    fontSize: 14,
    lineHeight: 22,
    textAlign: "center",
    marginTop: 8,
  },
});
