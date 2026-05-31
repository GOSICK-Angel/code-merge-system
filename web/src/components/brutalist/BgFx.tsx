interface Props {
  scanline?: 0 | 1 | 2;
}

export function BgFx({ scanline = 1 }: Props): JSX.Element {
  return (
    <div className="bg-fx" aria-hidden>
      <div className="glow-tl" />
      <div className="glow-br" />
      <div className="grid" />
      <div className={`scan intensity-${scanline}`} />
      <div className="sweep" />
    </div>
  );
}
