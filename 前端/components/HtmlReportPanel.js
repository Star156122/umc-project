import React, { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Platform, Pressable, Text, View } from "react-native";
import { apiRequest } from "../src/config/api";
import { marketColors as colors } from "../styles/marketTheme";

export default function HtmlReportPanel({ analysisId, title, available }) {
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setHtml(""); setError(""); setExpanded(false);
    if (!available || !analysisId) { setLoading(false); return () => { current = false; }; }
    setLoading(true);
    apiRequest(`/api/reports/analysis/${analysisId}/html`, { timeoutMs: 30000 })
      .then(data => { if (current) setHtml(data.html); })
      .catch(err => { if (current) setError(err.message || "讀取原始報告失敗"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [analysisId, available, retry]);
  const frame = full => React.createElement("iframe", {
    title: `${title} 原始 HTML 報告`, srcDoc: html,
    // Scripts support the original interactive chart; no access to the app or its login token.
    sandbox: "allow-scripts", referrerPolicy: "no-referrer",
    style: { width: "100%", height: full ? "100%" : 820, border: 0, background: "#fff", display: "block" },
  });
  return <View style={{ marginBottom: 20, borderRadius: 16, overflow: "hidden", backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }}>
    <View style={{ padding: 16, gap: 8 }}>
      <Text style={{ color: colors.text, fontSize: 18, fontWeight: "800" }}>原始 HTML 報告</Text>
      <Text style={{ color: colors.textMuted, lineHeight: 21 }}>查看原報告的圖表、交易紀錄與策略說明。圖表與樣式需要網路載入。</Text>
      {!available && <Text style={{ color: colors.textMuted }}>這份舊回測沒有保存 HTML。請在上方歷史報告中，選擇標記「含 HTML」的版本。</Text>}
      {loading && <ActivityIndicator color={colors.primary} accessibilityLabel="正在載入原始報告" />}
      {!!error && <><Text style={{ color: colors.danger }}>{error}</Text><Pressable onPress={() => setRetry(value => value + 1)}><Text style={{ color: colors.primary, paddingVertical: 8 }}>重新載入</Text></Pressable></>}
      {!!html && Platform.OS === "web" && <Pressable accessibilityRole="button" onPress={() => setExpanded(true)} style={{ alignSelf: "flex-start", backgroundColor: colors.primary, borderRadius: 10, padding: 12 }}><Text style={{ color: "#fff", fontWeight: "800" }}>放大閱讀報告</Text></Pressable>}
      {!!html && Platform.OS !== "web" && <Text style={{ color: colors.textMuted }}>請使用電腦或手機瀏覽器開啟網頁版，查看互動 HTML 報告。</Text>}
    </View>
    {!!html && Platform.OS === "web" && !expanded && frame(false)}
    <Modal visible={expanded} onRequestClose={() => setExpanded(false)} animationType="fade">
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <View style={{ padding: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <Text style={{ flex: 1, color: colors.text, fontSize: 18, fontWeight: "800" }}>{title} · 原始報告</Text>
          <Pressable accessibilityRole="button" onPress={() => setExpanded(false)} style={{ backgroundColor: colors.primary, padding: 12, borderRadius: 10 }}><Text style={{ color: "#fff", fontWeight: "800" }}>關閉放大</Text></Pressable>
        </View>
        {Platform.OS === "web" && <View style={{ flex: 1 }}>{frame(true)}</View>}
      </View>
    </Modal>
  </View>;
}
