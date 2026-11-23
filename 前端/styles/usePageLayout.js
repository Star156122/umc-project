import { useWindowDimensions } from "react-native";
import { layout, space, type } from "./theme";

// 手機寬度與桌面寬度共用的版面設定：桌面置中並限制最大寬度。
export default function usePageLayout() {
  const { width } = useWindowDimensions();
  const compact = width < layout.compactBreakpoint;
  return {
    compact,
    wide: width >= 960,
    page: {
      width: "100%",
      maxWidth: layout.maxWidth,
      alignSelf: "center",
      paddingHorizontal: compact ? space.sm : space.xl,
    },
    card: compact ? { padding: space.md } : null,
    title: compact ? { fontSize: 24, lineHeight: 32 } : null,
  };
}
