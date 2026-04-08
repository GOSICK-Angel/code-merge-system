import React from "react";
import { useAppStore } from "../state/store.js";
import { DashboardScreen } from "./DashboardScreen.js";
import { PlanReviewScreen } from "./PlanReviewScreen.js";
import { DecisionScreen } from "./DecisionScreen.js";
import { FileDetailScreen } from "./FileDetailScreen.js";
import { JudgeScreen } from "./JudgeScreen.js";
import { ReportScreen } from "./ReportScreen.js";

export function ScreenRouter() {
  const activeScreen = useAppStore((s) => s.activeScreen);

  switch (activeScreen) {
    case "dashboard":
      return <DashboardScreen />;
    case "plan_review":
      return <PlanReviewScreen />;
    case "decisions":
      return <DecisionScreen />;
    case "file_detail":
      return <FileDetailScreen />;
    case "judge":
      return <JudgeScreen />;
    case "report":
      return <ReportScreen />;
    default:
      return <DashboardScreen />;
  }
}
