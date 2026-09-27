import React from "react";
import { View, Text } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { styles } from "../styles/styles";

export default function Header() {
  return (
    <View style={styles.header}>
      <View style={styles.headerLogo}>
        <Ionicons name="stats-chart" size={22} color="white" />
      </View>

      <View>
        <Text style={styles.headerTitle}>AI 股票預測分析系統</Text>
        <Text style={styles.headerSub}>智慧分析・輔助決策</Text>
      </View>
    </View>
  );
}