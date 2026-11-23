import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, shadow, space, type } from "../styles/theme";

export default function MarketHeader({
  title,
  subtitle,
  icon = "pulse",
  rightContent,
  children,
  style,
}) {
  return (
    <View style={[styles.container, style]}>
      <View style={styles.topRow}>
        <View style={styles.textBox}>
          <Text style={styles.eyebrow}>STRATEGY LAB</Text>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.subtitle}>{subtitle}</Text>
        </View>

        {rightContent || (
          <View style={styles.iconBox}>
            <Ionicons name={icon} size={22} color={colors.primaryText} />
          </View>
        )}
      </View>

      {children ? <View style={styles.children}>{children}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: "100%",
    backgroundColor: colors.header,
    borderWidth: 1,
    borderColor: colors.headerAccent,
    borderRadius: radius.xl,
    padding: space.xl,
    marginBottom: space.md,
    overflow: "hidden",
    ...shadow,
  },
  topRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
  },
  textBox: {
    flex: 1,
    minWidth: 0,
  },
  eyebrow: { ...type.eyebrow, marginBottom: space.xs },
  title: { ...type.display, fontSize: 26, lineHeight: 34 },
  subtitle: { ...type.small, marginTop: space.xxs },
  iconBox: {
    width: 48,
    height: 48,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primarySoft,
    borderWidth: 1,
    borderColor: colors.headerAccent,
  },
  children: {
    marginTop: space.lg,
  },
});
