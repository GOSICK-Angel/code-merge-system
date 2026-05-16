import type { CSSProperties, ReactNode } from "react";

interface Props {
  title?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  accent?: boolean;
  pad?: boolean;
  style?: CSSProperties;
  className?: string;
}

export function Card({
  title,
  hint,
  children,
  accent = true,
  pad = true,
  style,
  className = "",
}: Props): JSX.Element {
  return (
    <div className={`card ${className}`.trim()} style={style}>
      {accent && <span className="corner lt" />}
      {accent && <span className="corner" />}
      {title && (
        <div className="card-title">
          <span className="t">{title}</span>
          {hint && (
            <span
              className="dimmer"
              style={{ fontFamily: "var(--mono)", fontSize: 10 }}
            >
              {hint}
            </span>
          )}
        </div>
      )}
      <div className={pad ? "body" : ""}>{children}</div>
    </div>
  );
}
