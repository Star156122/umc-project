import usePageLayout from '../styles/usePageLayout';
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
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import MarketHeader from "../components/MarketHeader";
import MarketStockRow from "../components/MarketStockRow";
import { apiRequest } from "../src/config/api";
import { colors as marketColors, radius, space, type, ui } from "../styles/theme";

export default function WatchListScreen() {
 const layout=usePageLayout();
  const [newCode,setNewCode]=useState(""),[adding,setAdding]=useState(false),[filter,setFilter]=useState("");
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

  async function addStock(){
    if(adding)return;
    if(!/^\d{4,6}[A-Za-z]?$/.test(newCode.trim())){setError("請輸入 4～6 位股票代號（ETF 可含英文字尾，如 00632R）");return;}
    setAdding(true);setError("");
    try{await apiRequest("/api/favorites",{method:"POST",body:JSON.stringify({code:newCode.trim()}),timeoutMs:30000});setNewCode("");await loadFavorites({silent:true});}catch(e){setError(e.message);}finally{setAdding(false);}
  }
  const header = (
    <View>
      <MarketHeader
        title="我的自選股"
        subtitle="追蹤你的研究清單，從個股直接進入策略模擬"
        icon="star"
        rightContent={
          <View style={styles.countBadge}>
            <Text style={styles.countValue}>{stocks.length}</Text>
            <Text style={styles.countLabel}>檔股票</Text>
          </View>
        }
      />

      <View style={styles.toolCard}>
        <View style={styles.toolRow}>
          <TextInput accessibilityLabel="新增自選股票代號" placeholder="輸入股票代號，例如 2330" placeholderTextColor={marketColors.textSubtle} value={newCode} onChangeText={setNewCode} keyboardType="number-pad" maxLength={6} style={[ui.input,styles.toolInput]}/>
          <Pressable disabled={adding} onPress={addStock} style={({pressed})=>[ui.buttonPrimary,pressed&&ui.buttonPrimaryPressed,adding&&ui.disabled]}><Text style={ui.buttonPrimaryText}>{adding?"加入中…":"加入自選股"}</Text></Pressable>
        </View>
        <TextInput accessibilityLabel="篩選自選股" placeholder="依代號或名稱篩選" placeholderTextColor={marketColors.textSubtle} value={filter} onChangeText={setFilter} style={ui.input}/>
      </View>
      <View style={styles.sectionRow}>
        <View style={styles.sectionTitleBox}>
          <Ionicons name="star" size={19} color={marketColors.star} />
          <Text style={styles.sectionTitle}>自選清單</Text>
        </View>
        <Text style={styles.updateText}>更新 {updateTimeText}</Text>
      </View>

      <View style={styles.infoBox}>
        <Ionicons name="information-circle-outline" size={16} color={marketColors.info} />
        <Text style={styles.infoText}>
          金色星號代表已加入自選；再次按下即可從資料庫移除。
        </Text>
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Ionicons name="alert-circle" size={19} color={marketColors.danger} />
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
        <Ionicons name="star-outline" size={34} color={marketColors.star} />
      </View>
      <Text style={styles.emptyTitle}>{filter.trim()?"沒有符合篩選的股票":"還沒有自選股"}</Text>
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
        data={stocks.filter(x=>(String(x.code)+String(x.name)).toLowerCase().includes(filter.trim().toLowerCase()))}
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
        contentContainerStyle={[styles.listContent,layout.page]}
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
  safeArea: { flex: 1, backgroundColor: marketColors.background },
  list: { width: "100%", alignSelf: "center" },
  listContent: { paddingTop: space.lg, paddingBottom: 120 },
  toolCard: {
    ...StyleSheet.flatten(ui.card),
    gap: space.sm,
    padding: space.md,
    marginBottom: space.lg,
  },
  toolRow: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  toolInput: { flex: 1, minWidth: 200 },
  countBadge: {
    minWidth: 60,
    height: 52,
    paddingHorizontal: space.sm,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.primarySoft,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
  },
  countValue: { ...type.title, ...type.number, lineHeight: 26 },
  countLabel: { ...type.caption, color: marketColors.textMuted, fontSize: 11 },
  sectionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: space.sm,
  },
  sectionTitleBox: { flexDirection: "row", alignItems: "center", gap: space.xs },
  sectionTitle: { ...type.title },
  updateText: { ...type.caption, ...type.number },
  infoBox: { ...StyleSheet.flatten([ui.notice, ui.noticeInfo]), alignItems: "center", marginBottom: space.sm },
  infoText: StyleSheet.flatten(ui.noticeInfoText),
  errorBox: { ...StyleSheet.flatten([ui.notice, ui.noticeError]), alignItems: "center", marginBottom: space.sm },
  errorText: StyleSheet.flatten(ui.noticeErrorText),
  retryText: { ...type.small, color: marketColors.primaryText, fontWeight: "700" },
  stateCard: {
    minHeight: 280,
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs,
    backgroundColor: marketColors.surface,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: radius.lg,
    paddingHorizontal: space.xxl,
  },
  stateText: { ...type.small, marginTop: space.xs },
  emptyIconBox: {
    width: 72,
    height: 72,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(252,201,63,0.10)",
    borderWidth: 1,
    borderColor: "rgba(252,201,63,0.35)",
  },
  emptyTitle: { ...type.heading, marginTop: space.sm },
  emptySubtitle: { ...type.small, textAlign: "center", maxWidth: 420 },
});
