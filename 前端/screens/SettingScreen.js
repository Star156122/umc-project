import usePageLayout from '../styles/usePageLayout';
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
import { colors as marketColors, radius, shadow, space, type, ui } from "../styles/theme";

import SettingsPanel from "../components/SettingsPanel";

function SettingItem({ icon, title, subtitle, onPress }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      onPress={onPress}
      style={({ pressed }) => [styles.item, pressed && styles.itemPressed]}
    >
      <View style={styles.itemLeft}>
        <View style={styles.iconBox}>
          <Ionicons name={icon} size={21} color={marketColors.primaryText} />
        </View>
        <View style={styles.itemTextBox}>
          <Text style={styles.itemTitle}>{title}</Text>
          <Text style={styles.itemSubtitle}>{subtitle}</Text>
        </View>
      </View>
      <Ionicons name="chevron-forward" size={20} color={marketColors.textSubtle} />
    </Pressable>
  );
}

export default function SettingScreen() {
 const layout=usePageLayout();
  const [section,setSection]=useState(null);
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
      {section&&<SettingsPanel section={section} onClose={()=>setSection(null)} onLogout={handleLogout}/>}
      <StatusBar barStyle="light-content" backgroundColor={marketColors.background} />
      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.content,layout.page]}>
          <MarketHeader
            title="系統設定"
            subtitle="管理帳號安全，設定你的預設股票、策略與報告閱讀方式"
            icon="settings"
          />

          <View style={styles.profileCard}>
            <View style={styles.avatar}>
              <Ionicons name="person" size={30} color={marketColors.primaryText} />
            </View>
            <View style={styles.profileTextBox}>
              <Text style={styles.name}>{user.name || user.username || "使用者"}</Text>
              <Text style={styles.email}>{user.email || "尚未登入"}</Text>
              <View style={styles.loginStatus}>
                <View style={styles.statusDot} />
                <Text style={styles.statusText}>目前登入帳號</Text>
              </View>
            </View>
          </View>

          <Text style={styles.sectionTitle}>帳號與個人化</Text>
          <View style={styles.sectionCard}>
            <SettingItem
              icon="shield-checkmark-outline"
              title="帳號與安全"
              onPress={()=>setSection("account")}
              subtitle="修改密碼與登入安全"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="notifications-outline"
              title="通知設定"
              onPress={()=>setSection("notifications")}
              subtitle="回測完成提醒與瀏覽器通知"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="star-outline"
              title="自選股管理"
              subtitle="查看、新增及移除收藏股票"
              onPress={() => router.push("/(tabs)/watch")}
            />
            <View style={styles.divider} />
            <SettingItem
              icon="options-outline"
              title="分析偏好"
              onPress={()=>setSection("analysis")}
              subtitle="預設策略與報告顯示內容"
            />
          </View>

          <Text style={styles.sectionTitle}>系統資訊</Text>
          <View style={styles.sectionCard}>
            <SettingItem
              icon="lock-closed-outline"
              title="資料與隱私"
              onPress={()=>setSection("privacy")}
              subtitle="資料使用設定與隱私管理"
            />
            <View style={styles.divider} />
            <SettingItem
              icon="information-circle-outline"
              title="關於系統"
              onPress={()=>setSection("about")}
              subtitle="行情來源為 Yahoo Finance"
            />
          </View>

          <View style={styles.versionCard}>
            <View style={styles.versionIcon}>
              <Ionicons name="cube-outline" size={21} color={marketColors.primaryText} />
            </View>
            <View style={styles.versionTextBox}>
              <Text style={styles.versionTitle}>系統版本</Text>
              <Text style={styles.versionText}>多策略回測與個人設定版</Text>
            </View>
          </View>

          <Pressable
            style={({ pressed }) => [styles.logoutButton, pressed && styles.logoutPressed]}
            onPress={handleLogout}
          >
            <Ionicons name="log-out-outline" size={20} color={marketColors.danger} />
            <Text style={styles.logoutText}>登出帳號</Text>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const card = {
  backgroundColor: marketColors.surface,
  borderRadius: radius.lg,
  borderWidth: 1,
  borderColor: marketColors.border,
  ...shadow,
};

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: marketColors.background },
  scrollView: { flex: 1, backgroundColor: marketColors.background },
  scrollContent: { paddingBottom: 120 },
  content: {
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
    paddingTop: space.lg,
  },
  profileCard: {
    ...card,
    padding: space.lg,
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    marginBottom: space.xl,
  },
  avatar: {
    width: 60,
    height: 60,
    borderRadius: radius.pill,
    backgroundColor: marketColors.primarySoft,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    justifyContent: "center",
    alignItems: "center",
  },
  profileTextBox: { flex: 1, minWidth: 0 },
  name: { ...type.title },
  email: { ...type.small, marginTop: 2 },
  loginStatus: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: space.xs },
  statusDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: marketColors.success },
  statusText: { ...type.caption, color: marketColors.successText, fontWeight: "600" },
  sectionTitle: { ...type.eyebrow, color: marketColors.textSubtle, marginBottom: space.xs, marginLeft: space.xxs },
  sectionCard: {
    ...card,
    paddingHorizontal: space.md,
    marginBottom: space.xl,
    overflow: "hidden",
  },
  item: {
    minHeight: 72,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: space.sm,
    gap: space.sm,
  },
  itemPressed: { opacity: 0.7 },
  itemLeft: { flex: 1, flexDirection: "row", alignItems: "center", gap: space.sm },
  itemTextBox: { flex: 1, minWidth: 0 },
  iconBox: StyleSheet.flatten(ui.iconTile),
  itemTitle: { ...type.subheading },
  itemSubtitle: { ...type.caption, marginTop: 2 },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: marketColors.border,
    marginLeft: 56,
  },
  versionCard: {
    ...StyleSheet.flatten([ui.notice, ui.noticeInfo]),
    padding: space.md,
    alignItems: "center",
    gap: space.sm,
    marginBottom: space.lg,
  },
  versionIcon: StyleSheet.flatten(ui.iconTile),
  versionTextBox: { flex: 1 },
  versionTitle: { ...type.caption, color: marketColors.textMuted },
  versionText: { ...type.subheading, marginTop: 2 },
  logoutButton: {
    minHeight: 50,
    backgroundColor: marketColors.dangerSurface,
    borderWidth: 1,
    borderColor: marketColors.dangerBorder,
    borderRadius: radius.md,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    gap: space.xs,
    marginBottom: space.xxl,
  },
  logoutPressed: { backgroundColor: "#3a1a20" },
  logoutText: { ...type.subheading, color: marketColors.dangerText, fontWeight: "700" },
});
