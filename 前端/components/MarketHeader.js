import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { marketColors } from "../styles/marketTheme";

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
          <Text style={{color:"#7db9ed",fontSize:11,letterSpacing:2,marginBottom:8}}>STRATEGY LAB</Text>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.subtitle}>{subtitle}</Text>
        </View>

        {rightContent || (
          <View style={styles.iconBox}>
            <Ionicons name={icon} size={24} color={marketColors.white} />
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
    backgroundColor: marketColors.header,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    borderRadius: 21,
    padding: 24,
    marginBottom: 14,
    overflow: "hidden",
  },
  topRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
  },
  textBox: {
    flex: 1,
    minWidth: 0,
    paddingRight: 12,
  },
  title: {
    color: marketColors.white,
    fontSize: 25,
    fontWeight: "900",
  },
  subtitle: {
    color: "#b7cfdb",
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4,
  },
  iconBox: {
    width: 48,
    height: 48,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.headerAccent,
    borderWidth: 1,
    borderColor: "#17688f",
  },
  children: {
    marginTop: 14,
  },
});
