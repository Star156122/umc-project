import React from "react";
import { View } from "react-native";

function normalizePoints(values, width, height, padding) {
  const numbers = values
    .map((item) => Number(typeof item === "object" ? item?.value : item))
    .filter(Number.isFinite);

  if (numbers.length < 2) return [];

  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const range = max - min || 1;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;

  return numbers.map((value, index) => ({
    x: padding + (index / (numbers.length - 1)) * usableWidth,
    y: padding + ((max - value) / range) * usableHeight,
  }));
}

export default function Sparkline({
  values = [],
  width = 150,
  height = 72,
  color = "#ef4444",
  baselineColor = "rgba(148, 163, 184, 0.36)",
}) {
  const padding = 6;
  const points = normalizePoints(values, width, height, padding);

  return (
    <View
      style={{
        width,
        height,
        position: "relative",
        overflow: "hidden",
        borderRadius: 8,
        backgroundColor: "#07111b",
      }}
    >
      <View
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: height / 2,
          height: 1,
          backgroundColor: baselineColor,
        }}
      />

      {[0.25, 0.5, 0.75].map((ratio) => (
        <View
          key={ratio}
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: width * ratio,
            width: 1,
            backgroundColor: "rgba(71, 85, 105, 0.24)",
          }}
        />
      ))}

      {points.slice(0, -1).map((point, index) => {
        const next = points[index + 1];
        const dx = next.x - point.x;
        const dy = next.y - point.y;
        const length = Math.sqrt(dx * dx + dy * dy);
        const angle = Math.atan2(dy, dx);

        return (
          <View
            key={`${index}-${point.x}`}
            style={{
              position: "absolute",
              left: (point.x + next.x) / 2 - length / 2,
              top: (point.y + next.y) / 2 - 1,
              width: length,
              height: 2,
              borderRadius: 2,
              backgroundColor: color,
              transform: [{ rotateZ: `${angle}rad` }],
            }}
          />
        );
      })}
    </View>
  );
}
