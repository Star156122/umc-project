import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";

const defaultApiUrl =
  Platform.OS === "android"
    ? "http://10.0.2.2:3000"
    : "http://localhost:3000";

export const API_URL =
  process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, "") || defaultApiUrl;

export async function apiRequest(path, options = {}) {
  const {
    timeoutMs = 10000,
    includeAuth = true,
    headers: customHeaders = {},
    ...fetchOptions
  } = options;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const token = includeAuth
      ? await AsyncStorage.getItem("authToken")
      : null;

    const response = await fetch(`${API_URL}${path}`, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...customHeaders,
      },
      signal: controller.signal,
    });

    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : { message: await response.text() };

    if (!response.ok) {
      const error = new Error(data.message || "伺服器回應失敗");
      error.status = response.status;
      error.data = data;
      throw error;
    }

    return data;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("連線逾時，請確認後端伺服器、回測資料來源或 Yahoo Finance 是否正常");
    }

    if (error instanceof TypeError) {
      throw new Error(
        `無法連接後端 ${API_URL}，請確認 API 位址與伺服器狀態`
      );
    }

    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}
