type Props = {
  level: number;
  ttsActive: boolean;
  label: string;
};

export function VoiceMeter({ level, ttsActive, label }: Props) {
  const pct = Math.round(level * 100);
  return (
    <div className="panel">
      <h2>{label}</h2>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            flex: 1,
            height: 12,
            borderRadius: 6,
            background: "var(--panel2)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${pct}%`,
              height: "100%",
              background: ttsActive ? "var(--warn)" : "var(--me)",
              transition: "width 80ms linear",
            }}
          />
        </div>
        <span className="mono" style={{ fontSize: 12, color: "var(--dim)", minWidth: 72 }}>
          {ttsActive ? "TTS duck" : `${pct}%`}
        </span>
      </div>
    </div>
  );
}
