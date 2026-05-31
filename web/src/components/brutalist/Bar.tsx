interface BarProps {
  pct: number;
  marching?: boolean;
}

export function Bar({ pct, marching = false }: BarProps): JSX.Element {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="bar">
      <div className="fill" style={{ right: `${100 - clamped}%` }} />
      {marching && <div className="march" />}
    </div>
  );
}

interface AsciiBarProps {
  pct: number;
  width?: number;
}

export function AsciiBar({ pct, width = 28 }: AsciiBarProps): JSX.Element {
  const clamped = Math.max(0, Math.min(100, pct));
  const filled = Math.round((clamped / 100) * width);
  const s = "█".repeat(filled) + "░".repeat(width - filled);
  return <span className="ascii-bar">{s}</span>;
}
