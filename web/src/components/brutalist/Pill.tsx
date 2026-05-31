import type { ReactNode } from "react";

export type PillTone = "" | "amber" | "green" | "orange" | "red" | "teal";

interface Props {
  tone?: PillTone;
  live?: boolean;
  children: ReactNode;
}

export function Pill({ tone = "", live = false, children }: Props): JSX.Element {
  return (
    <span className={`pill ${tone} ${live ? "live" : ""}`.trim()}>
      <span className="dot" />
      {children}
    </span>
  );
}
