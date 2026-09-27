import React from "react";
import { View, Text } from "react-native";
import { styles } from "../styles/styles";

export default function StockCard({ code, name, price, trend }) {
  return (
    <View style={styles.stockCard}>
      <Text style={styles.stockCode}>{code}</Text>
      <Text style={styles.stockName}>{name}</Text>
      <Text style={trend === "up" ? styles.up : styles.down}>{price}</Text>
    </View>
  );
}