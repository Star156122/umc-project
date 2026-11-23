import usePageLayout from '../styles/usePageLayout';
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
import { authBase } from "../styles/authStyles";

export default function AuthScreen() {
 const layout=usePageLayout();
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
        router.replace("/analysis");
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
          style={styles.scroll}
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.brandPanel}>
            <View style={styles.logoBox}>
              <Ionicons name="trending-up" size={30} color={marketColors.white} />
            </View>
            <View style={styles.brandTextBox}>
              <Text
                style={styles.title}

                adjustsFontSizeToFit
                minimumFontScale={0.72}
              >
                AI 股票預測分析系統
              </Text>
              <Text style={styles.subtitle}>策略模擬・交易回放・智慧分析</Text>
            </View>
          </View>

          <View style={[styles.loginCard,layout.card]}>
            <View style={styles.cardHeader}>
              <Text style={styles.cardTitle}>歡迎回來</Text>
              <Text style={styles.cardSubtitle}>登入策略研究工作台，查看模擬交易與歷史報告</Text>
            </View>

            <Text style={styles.label}>電子郵件</Text>
            <View style={styles.inputBox}>
              <Ionicons name="mail-outline" size={20} color={marketColors.textMuted} />
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
                size={20}
                color={marketColors.textMuted}
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
                  size={20}
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
                  size={18}
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
                  <Ionicons name="log-in-outline" size={20} color={marketColors.white} />
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
              <Ionicons name="person-add-outline" size={19} color={marketColors.primaryText} />
              <Text style={styles.registerButtonText}>註冊新帳號</Text>
            </Pressable>
          </View>

          <View style={styles.sourceNote}>
            <Ionicons name="information-circle-outline" size={16} color={marketColors.textSubtle} />
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
  ...authBase,
  loginCard: authBase.card,
  loginButton: authBase.primaryButton,
  pressedButton: authBase.primaryPressed,
  loginButtonText: authBase.primaryText,
  registerButton: authBase.secondaryButton,
  pressedRegisterButton: authBase.secondaryPressed,
  registerButtonText: authBase.secondaryText,
});
