import React from "react";
import { View, Text } from "react-native";
import { styles } from "../styles/styles";

export default function SmallStock({ code, name, result }) {
  return (
    <View style={styles.smallCard}>
      <View>
        <Text style={styles.stockCode}>
          {code} {name}
        </Text>
        <Text style={styles.stockName}>AI 預測結果</Text>
      </View>

      <Text
        style={
          result === "上漲"
            ? styles.up
            : result === "下跌"
            ? styles.down
            : styles.hold
        }
      >
        {result}
      </Text>
    </View>
  );
}