// 全站共用視覺設定：配色、間距、圓角、字體層級與常用元件樣式。
// 所有畫面與元件都從這裡取用，避免各頁面各自寫死色碼。
import { Platform, StyleSheet } from "react-native";

export const colors = {
  // 底色與表面
  background: "#0a1019",
  surface: "#111a26",
  surfaceRaised: "#172332",
  surfaceSunken: "#0c141e",
  surfacePressed: "#1d2b3c",
  input: "#0c141e",
  header: "#0f1e30",
  headerAccent: "#1a3350",
  overlay: "rgba(4,8,14,0.78)",
  // 線條
  border: "#223142",
  borderSoft: "#1a2634",
  borderStrong: "#34485e",
  // 主色
  primary: "#3b82f6",
  primaryPressed: "#2563eb",
  primarySoft: "rgba(59,130,246,0.14)",
  primaryText: "#8db9ff",
  // 文字
  text: "#eef3f9",
  textMuted: "#9fb0c2",
  textSubtle: "#71849a",
  textDisabled: "#56687b",
  // 台股慣例：紅漲綠跌
  rising: "#f0474f",
  falling: "#1fbf6a",
  // 狀態
  success: "#22c55e",
  successSurface: "#0e2a1e",
  successBorder: "#1d5a3a",
  successText: "#a7f0c4",
  danger: "#f87171",
  dangerSolid: "#c53030",
  dangerSolidPressed: "#a32626",
  dangerSurface: "#2a1419",
  dangerBorder: "#5c2129",
  dangerText: "#fecaca",
  warning: "#f5b544",
  warningSurface: "#2a2212",
  warningBorder: "#5a4419",
  warningText: "#fcd98a",
  info: "#60a5fa",
  infoSurface: "#0e1d2e",
  infoBorder: "#1d3a58",
  infoText: "#bfd7ef",
  star: "#fcc93f",
  // 圖表
  chartBackground: "#0b121c",
  chartGrid: "#1a2533",
  chartAxis: "#2c3b4d",
  white: "#ffffff",
};

// 網頁版：讓瀏覽器原生捲軸、表單控制項使用深色樣式，與介面一致。
if (Platform.OS === "web" && typeof document !== "undefined") {
  document.documentElement.style.colorScheme = "dark";
}

export const space = { xxs: 4, xs: 8, sm: 12, md: 16, lg: 20, xl: 24, xxl: 32 };
export const radius = { sm: 8, md: 12, lg: 16, xl: 20, pill: 999 };
export const layout = { maxWidth: 1120, compactBreakpoint: 600 };

const fontFamily = Platform.select({
  web: '"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,-apple-system,"Segoe UI",sans-serif',
  default: undefined,
});

// 字體層級：display > title > heading > subheading > body > small > caption
export const type = {
  display: { fontFamily, fontSize: 28, lineHeight: 36, fontWeight: "800", color: colors.text },
  title: { fontFamily, fontSize: 22, lineHeight: 30, fontWeight: "700", color: colors.text },
  heading: { fontFamily, fontSize: 18, lineHeight: 26, fontWeight: "700", color: colors.text },
  subheading: { fontFamily, fontSize: 16, lineHeight: 24, fontWeight: "600", color: colors.text },
  body: { fontFamily, fontSize: 15, lineHeight: 24, color: colors.text },
  small: { fontFamily, fontSize: 13, lineHeight: 21, color: colors.textMuted },
  caption: { fontFamily, fontSize: 12, lineHeight: 18, color: colors.textSubtle },
  eyebrow: { fontFamily, fontSize: 11, lineHeight: 16, fontWeight: "700", letterSpacing: 1.6, color: colors.primaryText },
  number: { fontFamily, fontVariant: ["tabular-nums"] },
};

export const shadow = Platform.select({
  web: { boxShadow: "0 1px 2px rgba(0,0,0,0.25), 0 8px 24px rgba(0,0,0,0.18)" },
  default: { elevation: 2 },
});

// 常用元件樣式
export const ui = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: space.xl,
    gap: space.md,
    ...shadow,
  },
  cardSunken: {
    backgroundColor: colors.surfaceSunken,
    borderWidth: 1,
    borderColor: colors.borderSoft,
    borderRadius: radius.md,
    padding: space.md,
    gap: space.xs,
  },
  hero: {
    backgroundColor: colors.header,
    borderWidth: 1,
    borderColor: colors.headerAccent,
    borderRadius: radius.xl,
    padding: space.xl,
    gap: space.sm,
    ...shadow,
  },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm },
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: space.xs, alignItems: "center" },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border },
  // 文字
  display: type.display,
  title: type.title,
  heading: type.heading,
  subheading: type.subheading,
  body: type.body,
  muted: { ...type.body, color: colors.textMuted },
  small: type.small,
  caption: type.caption,
  eyebrow: type.eyebrow,
  link: { ...type.body, color: colors.primaryText, fontWeight: "600" },
  // 按鈕
  buttonPrimary: {
    minHeight: 48,
    paddingHorizontal: space.lg,
    paddingVertical: space.sm,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs,
  },
  buttonPrimaryPressed: { backgroundColor: colors.primaryPressed },
  buttonPrimaryText: { ...type.subheading, color: colors.white, fontWeight: "700", textAlign: "center" },
  buttonSecondary: {
    minHeight: 44,
    paddingHorizontal: space.md,
    paddingVertical: space.xs,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceRaised,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs,
  },
  buttonSecondaryPressed: { backgroundColor: colors.surfacePressed },
  buttonSecondaryText: { ...type.body, color: colors.text, fontWeight: "600", textAlign: "center" },
  buttonGhost: { paddingVertical: space.xs, paddingHorizontal: space.xxs, alignSelf: "flex-start" },
  disabled: { opacity: 0.45 },
  // 選擇鈕
  chip: {
    minHeight: 40,
    paddingHorizontal: space.md,
    paddingVertical: space.xs,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceRaised,
    borderWidth: 1,
    borderColor: colors.border,
    justifyContent: "center",
  },
  chipSelected: { backgroundColor: colors.primarySoft, borderColor: colors.primary },
  chipText: { ...type.body, fontSize: 14, color: colors.text },
  chipTextSelected: { color: colors.primaryText, fontWeight: "700" },
  // 輸入框
  input: {
    minHeight: 48,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.input,
    color: colors.text,
    fontSize: 15,
    fontFamily,
  },
  label: { ...type.small, color: colors.textMuted, fontWeight: "600", marginBottom: space.xxs },
  // 提示框
  notice: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: space.xs,
    padding: space.sm,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  noticeInfo: { backgroundColor: colors.infoSurface, borderColor: colors.infoBorder },
  noticeSuccess: { backgroundColor: colors.successSurface, borderColor: colors.successBorder },
  noticeError: { backgroundColor: colors.dangerSurface, borderColor: colors.dangerBorder },
  noticeWarning: { backgroundColor: colors.warningSurface, borderColor: colors.warningBorder },
  noticeInfoText: { ...type.small, color: colors.infoText, flex: 1 },
  noticeSuccessText: { ...type.small, color: colors.successText, flex: 1 },
  noticeErrorText: { ...type.small, color: colors.dangerText, flex: 1 },
  noticeWarningText: { ...type.small, color: colors.warningText, flex: 1 },
  // 表格
  tableHeader: { flexDirection: "row", backgroundColor: colors.surfaceRaised, borderTopLeftRadius: radius.sm, borderTopRightRadius: radius.sm },
  tableRow: { flexDirection: "row", borderBottomWidth: StyleSheet.hairlineWidth, borderColor: colors.border },
  tableRowAlt: { backgroundColor: colors.surfaceSunken },
  tableCell: { ...type.small, color: colors.text, paddingHorizontal: space.sm, paddingVertical: 10, fontVariant: ["tabular-nums"] },
  tableHeadCell: { ...type.small, color: colors.textMuted, fontWeight: "700", paddingHorizontal: space.sm, paddingVertical: 10 },
  // 標籤
  badge: {
    alignSelf: "flex-start",
    paddingHorizontal: space.xs,
    paddingVertical: 2,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
  },
  badgeText: { ...type.caption, color: colors.primaryText, fontWeight: "700" },
  iconTile: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primarySoft,
  },
});
