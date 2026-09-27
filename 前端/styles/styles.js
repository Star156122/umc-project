import { StyleSheet } from "react-native";

export const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: "#f5f8ff",
    padding: 20,
  },
  title: {
    fontSize: 28,
    fontWeight: "bold",
    color: "#0b3578",
    marginBottom: 20,
    marginTop: 20,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: "bold",
    color: "#0b3578",
    marginTop: 24,
    marginBottom: 12,
  },
  search: {
    backgroundColor: "white",
    padding: 14,
    borderRadius: 14,
    marginBottom: 18,
  },
  grid: {
    flexDirection: "row",
    gap: 12,
  },
  stockCard: {
    flex: 1,
    backgroundColor: "white",
    padding: 18,
    borderRadius: 18,
    elevation: 3,
  },
  card: {
    backgroundColor: "white",
    padding: 18,
    borderRadius: 18,
    elevation: 3,
    gap: 8,
  },
  blueCard: {
    backgroundColor: "#0067e8",
    padding: 22,
    borderRadius: 20,
    elevation: 4,
  },
  whiteTitle: {
    color: "white",
    fontSize: 22,
    fontWeight: "bold",
    marginBottom: 10,
  },
  whiteText: {
    color: "white",
    fontSize: 18,
    marginBottom: 6,
  },
  stockCode: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#0b3578",
  },
  stockName: {
    color: "#6b7280",
    marginVertical: 4,
  },
  up: {
    color: "#16a34a",
    fontWeight: "bold",
    fontSize: 18,
  },
  down: {
    color: "#dc2626",
    fontWeight: "bold",
    fontSize: 18,
  },
  hold: {
    color: "#f59e0b",
    fontWeight: "bold",
    fontSize: 18,
  },
  smallCard: {
    backgroundColor: "white",
    padding: 16,
    borderRadius: 16,
    marginBottom: 12,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    elevation: 2,
  },
  settingItem: {
    backgroundColor: "white",
    padding: 18,
    borderRadius: 16,
    marginBottom: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    elevation: 2,
  },
  settingText: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#374151",
  },
});