import React, { useEffect, useState } from "react";
import {
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { router } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import MarketHeader from "../components/MarketHeader";
import { marketColors } from "../styles/marketTheme";

function SettingItem({ icon, title, subtitle, onPress }) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.item, pressed && styles.itemPressed]}
    >
      <View style={styles.itemLeft}>
        <View style={styles.iconBox}>
          <Ionicons name={icon} size={25} color="#78b7ff" />
        </View>
        <View style={styles.itemTextBox}>
          <Text style={styles.itemTitle}>{title}</Text>
          <Text style={styles.itemSubtitle}>{subtitle}</Text>
        </View>
      </View>
      <Ionicons name="chevron-forward" size={22} color={marketColors.textSubtle} />
    </Pressable>
  );
}

export default function SettingScreen() {
  const [user, setUser] = useState({ name: "使用者", email: "尚未登入" });

  useEffect(() => {
    const loadUser = async () => {
      try {
        const data = await AsyncStorage.getItem("currentUser");
        if (data) setUser(JSON.parse(data));
      } catch (_error) {
        setUser({ name: "使用者", email: "讀取帳號資料失敗" });
      }
    };
    loadUser();
  }, []);

  const handleLogout = async () => {
    await AsyncStorage.multiRemove(["currentUser", "authToken"]);
    router.replace("/login");
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor={marketColors.background} />
      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.content}>
          <MarketHeader
            title="系統設定"
            subtitle="帳號、自選股、通知與分析偏好"
            icon="settings"
          />

          <View style={styles.profileCard}>
            <View style={styles.avatar}>
              <Ionicons name="person" size={38} color="#78b7ff" />
            </View>
            <View style={styles.profileTextBox}>
              <Text style={styles.name}>{user.name || user.username || "使用者"}</Text>
              <Text style={styles.email}>{user.email || "尚未登入"}</Text>
              <View style={styles.loginStatus}>
                <View style={styles.statusDot} />
                <Text style={styles.statusText}>帳號已連接 MySQL</Text>
              </View>
            </View>
          </View>

          <Text style={styles.sectionTitle}>帳號與個人化</Text>
          <View style={styles.sectionCard}>
            <SettingItem
              icon="shield-checkmark-outline"
              title="帳號與安全"
              subtitle="修改密碼與登入安全"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="notifications-outline"
              title="通知設定"
              subtitle="買賣訊號與分析完成提醒"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="star-outline"
              title="自選股管理"
              subtitle="管理 favorite_stocks 收藏標的"
              onPress={() => router.push("/(tabs)/watch")}
            />
            <View style={styles.divider} />
            <SettingItem
              icon="options-outline"
              title="分析偏好"
              subtitle="預設策略與報告顯示內容"
            />
          </View>

          <Text style={styles.sectionTitle}>系統資訊</Text>
          <View style={styles.sectionCard}>
            <SettingItem
              icon="lock-closed-outline"
              title="資料與隱私"
              subtitle="資料使用設定與隱私管理"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="information-circle-outline"
              title="關於系統"
              subtitle="行情來源為 Yahoo Finance"
            />
          </View>

          <View style={styles.versionCard}>
            <View style={styles.versionIcon}>
              <Ionicons name="cube-outline" size={25} color="#78b7ff" />
            </View>
            <View style={styles.versionTextBox}>
              <Text style={styles.versionTitle}>系統版本</Text>
              <Text style={styles.versionText}>v1.2.0・中英名稱自動顯示版</Text>
            </View>
          </View>

          <Pressable
            style={({ pressed }) => [styles.logoutButton, pressed && styles.logoutPressed]}
            onPress={handleLogout}
          >
            <Ionicons name="log-out-outline" size={23} color={marketColors.white} />
            <Text style={styles.logoutText}>登出帳號</Text>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: marketColors.background,
  },
  scrollView: {
    flex: 1,
    backgroundColor: marketColors.background,
  },
  scrollContent: {
    paddingBottom: 120,
  },
  content: {
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
    paddingHorizontal: 12,
    paddingTop: 14,
  },
  profileCard: {
    backgroundColor: marketColors.surface,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: marketColors.border,
    padding: 17,
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 18,
  },
  avatar: {
    width: 70,
    height: 70,
    borderRadius: 35,
    backgroundColor: marketColors.surfaceRaised,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },
  profileTextBox: { flex: 1 },
  name: {
    fontSize: 22,
    fontWeight: "900",
    color: marketColors.text,
  },
  email: {
    fontSize: 14,
    color: marketColors.textMuted,
    marginTop: 4,
  },
  loginStatus: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 9,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: marketColors.success,
    marginRight: 6,
  },
  statusText: {
    color: "#b9d9ee",
    fontSize: 11,
    fontWeight: "700",
  },
  sectionTitle: {
    fontSize: 15,
    color: marketColors.textMuted,
    fontWeight: "800",
    marginBottom: 9,
    marginLeft: 4,
  },
  sectionCard: {
    backgroundColor: marketColors.surface,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: marketColors.border,
    paddingHorizontal: 13,
    marginBottom: 18,
    overflow: "hidden",
  },
  item: {
    minHeight: 82,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 2,
  },
  itemPressed: { opacity: 0.72 },
  itemLeft: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
  },
  itemTextBox: { flex: 1 },
  iconBox: {
    width: 48,
    height: 48,
    borderRadius: 15,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: marketColors.surfaceRaised,
    marginRight: 12,
  },
  itemTitle: {
    fontSize: 17,
    fontWeight: "900",
    color: marketColors.text,
  },
  itemSubtitle: {
    fontSize: 12,
    lineHeight: 18,
    color: marketColors.textSubtle,
    marginTop: 4,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: marketColors.borderSoft,
    marginLeft: 60,
  },
  versionCard: {
    backgroundColor: "#0b2436",
    borderRadius: 17,
    borderWidth: 1,
    borderColor: "#17425c",
    padding: 15,
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 14,
  },
  versionIcon: {
    width: 46,
    height: 46,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketColors.headerAccent,
    marginRight: 12,
  },
  versionTextBox: { flex: 1 },
  versionTitle: {
    fontSize: 13,
    color: marketColors.textMuted,
  },
  versionText: {
    fontSize: 15,
    fontWeight: "900",
    color: marketColors.text,
    marginTop: 4,
  },
  logoutButton: {
    height: 58,
    backgroundColor: "#b91c1c",
    borderRadius: 16,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 30,
  },
  logoutPressed: { backgroundColor: "#991b1b" },
  logoutText: {
    color: marketColors.white,
    fontSize: 18,
    fontWeight: "900",
    marginLeft: 8,
  },
});
