import React from "react";
import { View, Text, TouchableOpacity } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { styles } from "../styles/styles";

function Tab({ icon, label, active, onPress }) {
  return (
    <TouchableOpacity style={styles.tab} onPress={onPress}>
      <Ionicons name={icon} size={22} color={active ? "#2563eb" : "#999"} />
      <Text style={{ color: active ? "#2563eb" : "#999", fontSize: 12 }}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

export default function TabBar({ page, setPage }) {
  return (
    <View style={styles.tabBar}>
      <Tab
        icon="home"
        label="首頁"
        active={page === "home"}
        onPress={() => setPage("home")}
      />

      <Tab
        icon="bar-chart"
        label="分析"
        active={page === "analysis"}
        onPress={() => setPage("analysis")}
      />

      <Tab
        icon="star"
        label="自選股"
        active={page === "watch"}
        onPress={() => setPage("watch")}
      />

      <Tab
        icon="settings"
        label="設定"
        active={page === "setting"}
        onPress={() => setPage("setting")}
      />
    </View>
  );
}