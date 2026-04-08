import React from "react";
import { Text } from "ink";

type BadgeVariant = "success" | "warning" | "danger" | "info" | "muted";

interface BadgeProps {
  label: string;
  variant?: BadgeVariant;
}

const VARIANT_COLORS: Record<BadgeVariant, string> = {
  success: "green",
  warning: "yellow",
  danger: "red",
  info: "cyan",
  muted: "gray",
};

export function Badge({ label, variant = "info" }: BadgeProps) {
  const color = VARIANT_COLORS[variant];
  return (
    <Text color={color} bold>
      [{label}]
    </Text>
  );
}

export function riskToBadgeVariant(risk: string): BadgeVariant {
  switch (risk) {
    case "auto_safe":
    case "deleted_only":
      return "success";
    case "auto_risky":
      return "warning";
    case "human_required":
      return "danger";
    default:
      return "muted";
  }
}
