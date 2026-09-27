import React, { useState } from "react";
import { SafeAreaView, ScrollView } from "react-native";

import AuthScreen from "./screens/AuthScreen";
import HomeScreen from "./screens/HomeScreen";
import AnalysisScreen from "./screens/AnalysisScreen";
import WatchListScreen from "./screens/WatchListScreen";
import SettingScreen from "./screens/SettingScreen";

import Header from "./components/Header";
import TabBar from "./components/TabBar";
import { styles } from "./styles/styles";

export default function App() {
  const [page, setPage] = useState("login");

  if (page === "login") {
    return <AuthScreen setPage={setPage} />;
  }

  return (
    <SafeAreaView style={styles.app}>
      <ScrollView contentContainerStyle={{ paddingBottom: 90 }}>
        <Header />

        {page === "home" && <HomeScreen setPage={setPage} />}
        {page === "analysis" && <AnalysisScreen />}
        {page === "watch" && <WatchListScreen />}
        {page === "setting" && <SettingScreen />}
      </ScrollView>

      <TabBar page={page} setPage={setPage} />
    </SafeAreaView>
  );
}