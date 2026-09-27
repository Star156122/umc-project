import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StatusBar,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { apiRequest } from "../src/config/api";
import { marketColors } from "../styles/marketTheme";

export default function RegisterScreen() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("error");
  const [loading, setLoading] = useState(false);

  const showError = (text) => {
    setMessageType("error");
    setMessage(text);
  };

  const handleRegister = async () => {
    const cleanName = name.trim();
    const cleanEmail = email.trim().toLowerCase();
    setMessage("");

    if (!cleanName || !cleanEmail || !password || !confirmPassword) {
      showError("請完整輸入姓名、電子郵件、密碼與確認密碼");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanEmail)) {
      showError("電子郵件格式不正確");
      return;
    }
    if (password.length < 6) {
      showError("密碼至少需要 6 個字元");
      return;
    }
    if (password !== confirmPassword) {
      showError("兩次輸入的密碼不一致");
      return;
    }

    setLoading(true);
    try {
      await apiRequest("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ username: cleanName, email: cleanEmail, password }),
      });
      setMessageType("success");
      setMessage("註冊成功，正在返回登入頁…");
      setTimeout(() => router.replace("/login"), 700);
    } catch (error) {
      showError(error.message || "建立帳號失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  };

  const fields = [
    {
      key: "name",
      label: "姓名",
      icon: "person-outline",
      value: name,
      onChangeText: setName,
      placeholder: "請輸入姓名",
    },
    {
      key: "email",
      label: "電子郵件",
      icon: "mail-outline",
      value: email,
      onChangeText: setEmail,
      placeholder: "請輸入電子郵件",
      keyboardType: "email-address",
    },
  ];

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor={marketColors.header} />
      <KeyboardAvoidingView
        style={styles.keyboardView}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          style={styles.container}
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <Pressable
            style={styles.backButton}
            onPress={() => router.replace("/login")}
            disabled={loading}
          >
            <Ionicons name="arrow-back" size={22} color="#78b7ff" />
            <Text style={styles.backButtonText}>返回登入頁</Text>
          </Pressable>

          <View style={styles.brandPanel}>
            <View style={styles.logoBox}>
              <Ionicons name="person-add-outline" size={38} color={marketColors.white} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>建立帳號</Text>
              <Text style={styles.subtitle}>註冊後即可使用行情、自選股與分析功能</Text>
            </View>
          </View>

          <View style={styles.card}>
            {fields.map((field) => (
              <View key={field.key}>
                <Text style={styles.label}>{field.label}</Text>
                <View style={styles.inputBox}>
                  <Ionicons name={field.icon} size={22} color={marketColors.primary} />
                  <TextInput
                    style={styles.input}
                    placeholder={field.placeholder}
                    placeholderTextColor={marketColors.textSubtle}
                    value={field.value}
                    onChangeText={(text) => {
                      field.onChangeText(text);
                      setMessage("");
                    }}
                    keyboardType={field.keyboardType}
                    autoCapitalize="none"
                    autoCorrect={false}
                    editable={!loading}
                  />
                </View>
              </View>
            ))}

            <Text style={styles.label}>密碼</Text>
            <View style={styles.inputBox}>
              <Ionicons name="lock-closed-outline" size={22} color={marketColors.primary} />
              <TextInput
                style={styles.input}
                placeholder="至少輸入 6 個字元"
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
              />
              <Pressable style={styles.eyeButton} onPress={() => setShowPassword((v) => !v)}>
                <Ionicons
                  name={showPassword ? "eye-off-outline" : "eye-outline"}
                  size={23}
                  color={marketColors.textMuted}
                />
              </Pressable>
            </View>

            <Text style={styles.label}>確認密碼</Text>
            <View style={styles.inputBox}>
              <Ionicons
                name="shield-checkmark-outline"
                size={22}
                color={marketColors.primary}
              />
              <TextInput
                style={styles.input}
                placeholder="請再次輸入密碼"
                placeholderTextColor={marketColors.textSubtle}
                value={confirmPassword}
                onChangeText={(text) => {
                  setConfirmPassword(text);
                  setMessage("");
                }}
                secureTextEntry={!showConfirmPassword}
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
                returnKeyType="done"
                onSubmitEditing={handleRegister}
              />
              <Pressable
                style={styles.eyeButton}
                onPress={() => setShowConfirmPassword((v) => !v)}
              >
                <Ionicons
                  name={showConfirmPassword ? "eye-off-outline" : "eye-outline"}
                  size={23}
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
                styles.registerButton,
                pressed && styles.pressedButton,
                loading && styles.disabledButton,
              ]}
              onPress={handleRegister}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color={marketColors.white} />
              ) : (
                <>
                  <Ionicons name="person-add" size={23} color={marketColors.white} />
                  <Text style={styles.registerButtonText}>建立帳號</Text>
                </>
              )}
            </Pressable>

            <Pressable
              style={({ pressed }) => [styles.loginButton, pressed && styles.loginPressed]}
              onPress={() => router.replace("/login")}
              disabled={loading}
            >
              <Text style={styles.loginButtonText}>已有帳號？回登入頁</Text>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: marketColors.background },
  keyboardView: { flex: 1 },
  container: { flex: 1, backgroundColor: marketColors.background },
  content: {
    flexGrow: 1,
    width: "100%",
    maxWidth: 640,
    alignSelf: "center",
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 42,
  },
  backButton: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    marginBottom: 10,
  },
  backButtonText: {
    color: "#78b7ff",
    fontSize: 16,
    fontWeight: "800",
    marginLeft: 6,
  },
  brandPanel: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: marketColors.header,
    borderWidth: 1,
    borderColor: marketColors.headerAccent,
    borderRadius: 22,
    padding: 17,
    marginBottom: 15,
  },
  logoBox: {
    width: 66,
    height: 66,
    borderRadius: 20,
    backgroundColor: marketColors.headerAccent,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 14,
  },
  title: {
    color: marketColors.text,
    fontSize: 26,
    fontWeight: "900",
  },
  subtitle: {
    color: "#b7cfdb",
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4,
  },
  card: {
    width: "100%",
    backgroundColor: marketColors.surface,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: marketColors.border,
    padding: 20,
  },
  label: {
    color: "#c7d5df",
    fontSize: 14,
    fontWeight: "800",
    marginBottom: 8,
    marginLeft: 2,
  },
  inputBox: {
    minHeight: 60,
    backgroundColor: marketColors.input,
    borderWidth: 1,
    borderColor: marketColors.border,
    borderRadius: 16,
    paddingHorizontal: 15,
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 16,
  },
  input: {
    flex: 1,
    minHeight: 58,
    color: marketColors.text,
    fontSize: 16,
    marginLeft: 11,
    paddingVertical: 0,
  },
  eyeButton: {
    width: 42,
    height: 48,
    alignItems: "center",
    justifyContent: "center",
  },
  messageBox: {
    minHeight: 48,
    borderWidth: 1,
    borderRadius: 13,
    paddingHorizontal: 13,
    paddingVertical: 10,
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 16,
  },
  errorBox: { backgroundColor: marketColors.dangerSurface, borderColor: "#7f1d1d" },
  successBox: { backgroundColor: marketColors.successSurface, borderColor: "#166534" },
  messageText: { flex: 1, fontSize: 14, fontWeight: "700", marginLeft: 8 },
  errorText: { color: "#fecaca" },
  successText: { color: "#bbf7d0" },
  registerButton: {
    height: 62,
    backgroundColor: marketColors.primary,
    borderRadius: 16,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
  },
  pressedButton: { backgroundColor: marketColors.primaryPressed },
  disabledButton: { opacity: 0.62 },
  registerButtonText: {
    color: marketColors.white,
    fontSize: 20,
    fontWeight: "900",
    marginLeft: 8,
  },
  loginButton: {
    height: 54,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 10,
    borderRadius: 14,
  },
  loginPressed: { backgroundColor: marketColors.surfaceRaised },
  loginButtonText: {
    color: "#78b7ff",
    fontSize: 16,
    fontWeight: "800",
  },
});
