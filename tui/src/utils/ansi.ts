import chalk, { type ChalkInstance } from "chalk";

export const colors = {
  success: chalk.green,
  warning: chalk.yellow,
  danger: chalk.red,
  info: chalk.cyan,
  muted: chalk.gray,
  accent: chalk.magenta,
  primary: chalk.blue,
  bold: chalk.bold,
  dim: chalk.dim,
};

export function riskColor(level: string): ChalkInstance {
  switch (level) {
    case "auto_safe":
    case "deleted_only":
      return chalk.green;
    case "auto_risky":
      return chalk.yellow;
    case "human_required":
      return chalk.red;
    case "binary":
    case "excluded":
      return chalk.gray;
    default:
      return chalk.white;
  }
}

export function statusColor(status: string): ChalkInstance {
  switch (status) {
    case "completed":
      return chalk.green;
    case "failed":
      return chalk.red;
    case "awaiting_human":
      return chalk.yellow;
    case "planning":
    case "plan_reviewing":
    case "plan_revising":
    case "auto_merging":
    case "analyzing_conflicts":
    case "judge_reviewing":
    case "generating_report":
      return chalk.cyan;
    case "initialized":
      return chalk.blue;
    default:
      return chalk.white;
  }
}

export function verdictColor(verdict: string): ChalkInstance {
  switch (verdict) {
    case "pass":
      return chalk.green;
    case "conditional":
      return chalk.yellow;
    case "fail":
      return chalk.red;
    default:
      return chalk.white;
  }
}
