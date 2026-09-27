import React, { useState } from "react";
import {
  SafeAreaView,
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StatusBar,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { apiRequest } from "../src/config/api";
import { marketColors } from "../styles/marketTheme";

export default function AuthScreen() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("error");
  const [loading, setLoading] = useState(false);

  const showError = (text) => {
    setMessageType("error");
    setMessage(text);
  };

  const handleLogin = async () => {
    const cleanEmail = email.trim().toLowerCase();
    setMessage("");

    if (!cleanEmail || !password) {
      showError("請輸入電子郵件與密碼");
      return;
    }

    setLoading(true);

    try {
      const data = await apiRequest("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: cleanEmail, password }),
      });

      await AsyncStorage.multiSet([
        ["authToken", data.token],
        ["currentUser", JSON.stringify(data.user)],
      ]);

      setMessageType("success");
      setMessage("登入成功，正在進入系統…");

      setTimeout(() => {
        router.replace("/(tabs)");
      }, 400);
    } catch (error) {
      if (error.status === 404) {
        showError("沒有此帳號，請先註冊帳號");
      } else if (error.status === 401) {
        showError("密碼錯誤，請重新輸入");
      } else {
        showError(error.message || "登入失敗，請稍後再試");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor={marketColors.header} />
      <KeyboardAvoidingView
        style={styles.keyboardView}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.brandPanel}>
            <View style={styles.logoBox}>
              <Ionicons name="trending-up" size={44} color={marketColors.white} />
            </View>
            <View style={styles.brandTextBox}>
              <Text
                style={styles.title}
                numberOfLines={1}
                adjustsFontSizeToFit
                minimumFontScale={0.72}
              >
                AI 股票預測分析系統
              </Text>
              <Text style={styles.subtitle}>即時行情・策略回測・自選管理</Text>
            </View>
          </View>

          <View style={styles.loginCard}>
            <View style={styles.cardHeader}>
              <Text style={styles.cardTitle}>歡迎回來</Text>
              <Text style={styles.cardSubtitle}>登入後查看行情與分析紀錄</Text>
            </View>

            <Text style={styles.label}>電子郵件</Text>
            <View style={styles.inputBox}>
              <Ionicons name="mail-outline" size={23} color={marketColors.primary} />
              <TextInput
                style={styles.input}
                placeholder="請輸入電子郵件"
                placeholderTextColor={marketColors.textSubtle}
                value={email}
                onChangeText={(text) => {
                  setEmail(text);
                  setMessage("");
                }}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
              />
            </View>

            <Text style={styles.label}>密碼</Text>
            <View style={styles.inputBox}>
              <Ionicons
                name="lock-closed-outline"
                size={23}
                color={marketColors.primary}
              />
              <TextInput
                style={styles.input}
                placeholder="請輸入密碼"
                placeholderTextColor={marketColors.textSubtle}
                value={password}
                onChangeText={(text) => {
                  setPassword(text);
                  setMessage("");
                }}
                secureTextEntry={!showPassword}
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
                returnKeyType="done"
                onSubmitEditing={handleLogin}
              />
              <Pressable
                style={styles.eyeButton}
                onPress={() => setShowPassword((value) => !value)}
              >
                <Ionicons
                  name={showPassword ? "eye-off-outline" : "eye-outline"}
                  size={24}
                  color={marketColors.textMuted}
                />
              </Pressable>
            </View>

            {message ? (
              <View
                style={[
                  styles.messageBox,
                  messageType === "success" ? styles.successBox : styles.errorBox,
                ]}
              >
                <Ionicons
                  name={messageType === "success" ? "checkmark-circle" : "alert-circle"}
                  size={21}
                  color={
                    messageType === "success"
                      ? marketColors.success
                      : marketColors.danger
                  }
                />
                <Text
                  style={[
                    styles.messageText,
                    messageType === "success" ? styles.successText : styles.errorText,
                  ]}
                >
                  {message}
                </Text>
              </View>
            ) : null}

            <Pressable
              style={({ pressed }) => [
                styles.loginButton,
                pressed && styles.pressedButton,
                loading && styles.disabledButton,
              ]}
              onPress={handleLogin}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color={marketColors.white} />
              ) : (
                <>
                  <Ionicons name="log-in-outline" size={23} color={marketColors.white} />
                  <Text style={styles.loginButtonText}>登入系統</Text>
                </>
              )}
            </Pressable>

            <Pressable
              style={({ pressed }) => [
                styles.registerButton,
                pressed && styles.pressedRegisterButton,
              ]}
              onPress={() => router.push("/register")}
              disabled={loading}
            >
              <Ionicons name="person-add-outline" size={22} color={marketColors.primary} />
              <Text style={styles.registerButtonText}>註冊新帳號</Text>
            </Pressable>
          </View>

          <View style={styles.sourceNote}>
            <Ionicons name="information-circle-outline" size={18} color="#74b9ff" />
            <Text style={styles.bottomText}>
              股票行情由 Yahoo Finance 提供；自選股與帳號資料儲存在 MySQL。
            </Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: marketColors.background,
  },
  keyboardView: { flex: 1 },
  container: {
    flexGrow: 1,
    width: "100%",
    maxWidth: 620,
    alignSelf: "center",
    justifyContent: "center",
    paddingHorizontal: 20,
    paddingTop: 32,
    paddingBottom: 38,
  },
  brandPanel: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: marketColors.header,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    borderRadius: 24,
    padding: 18,
    marginBottom: 16,
  },
  logoBox: {
    width: 72,
    height: 72,
    borderRadius: 22,
    backgroundColor: marketColors.headerAccent,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 14,
  },
  brandTextBox: { flex: 1 },
  title: {
    color: marketColors.text,
    fontSize: 25,
    fontWeight: "900",
  },
  subtitle: {
    color: "#b7cfdb",
    fontSize: 13,
    lineHeight: 19,
    marginTop: 5,
  },
  loginCard: {
    width: "100%",
    backgroundColor: marketColors.surface,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: marketColors.border,
    padding: 20,
  },
  cardHeader: { marginBottom: 19 },
  cardTitle: {
    color: marketColors.text,
    fontSize: 24,
    fontWeight: "900",
  },
  cardSubtitle: {
    color: marketColors.textMuted,
    fontSize: 14,
    marginTop: 5,
  },
  label: {
    color: "#c7d5df",
    fontSize: 14,
    fontWeight: "800",
    marginBottom: 8,
    marginLeft: 2,
  },
  inputBox: {
    minHeight: 62,
    backgroundColor: marketColors.input,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 16,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 15,
    marginBottom: 16,
  },
  input: {
    flex: 1,
    minHeight: 60,
    color: marketColors.text,
    fontSize: 17,
    marginLeft: 11,
    paddingVertical: 0,
  },
  eyeButton: {
    width: 42,
    height: 50,
    alignItems: "center",
    justifyContent: "center",
  },
  messageBox: {
    minHeight: 48,
    borderRadius: 13,
    borderWidth: 1,
    paddingHorizontal: 13,
    paddingVertical: 10,
    marginBottom: 15,
    flexDirection: "row",
    alignItems: "center",
  },
  errorBox: {
    backgroundColor: marketColors.dangerSurface,
    borderColor: "#7f1d1d",
  },
  successBox: {
    backgroundColor: marketColors.successSurface,
    borderColor: "#166534",
  },
  messageText: {
    flex: 1,
    fontSize: 14,
    fontWeight: "700",
    marginLeft: 8,
  },
  errorText: { color: "#fecaca" },
  successText: { color: "#bbf7d0" },
  loginButton: {
    height: 62,
    backgroundColor: marketColors.primary,
    borderRadius: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
    marginBottom: 13,
  },
  pressedButton: { backgroundColor: marketColors.primaryPressed },
  disabledButton: { opacity: 0.62 },
  loginButtonText: {
    color: marketColors.white,
    fontSize: 20,
    fontWeight: "900",
    marginLeft: 8,
  },
  registerButton: {
    height: 58,
    backgroundColor: marketColors.surfaceRaised,
    borderWidth: 1,
    borderColor: marketColors.primary,
    borderRadius: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
  },
  pressedRegisterButton: { backgroundColor: marketColors.surfacePressed },
  registerButtonText: {
    color: "#78b7ff",
    fontSize: 17,
    fontWeight: "900",
    marginLeft: 8,
  },
  sourceNote: {
    flexDirection: "row",
    alignItems: "flex-start",
    backgroundColor: "#0b2436",
    borderWidth: 1,
    borderColor: "#17425c",
    borderRadius: 14,
    padding: 12,
    marginTop: 15,
  },
  bottomText: {
    flex: 1,
    color: "#b9d9ee",
    fontSize: 12,
    lineHeight: 18,
    marginLeft: 7,
  },
});
