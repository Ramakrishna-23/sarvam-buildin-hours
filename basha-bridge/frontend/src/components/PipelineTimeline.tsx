import type { BusEvent, LatencyRow } from "../types";

type Props = {
  events: BusEvent[];
  latencyRows: LatencyRow[];
};

export function PipelineTimeline({ events, latencyRows }: Props) {
  const spoken = events.filter((e) => e.tag === "segment.spoken");
  const maxEar = Math.max(0, ...latencyRows.map((r) => r.ear_ms ?? 0), 2000);

  return (
    <div className="panel">
      <h2>Pipeline timeline</h2>
      <div style={{ fontSize: 12, color: "var(--dim)", marginBottom: 10 }}>
        STT partials → chunker commit → Mayura translate → Bulbul TTS → ear-to-ear
      </div>
      {latencyRows.length === 0 ? (
        <div style={{ color: "var(--dim)" }}>Run a fixture or join live to see segment bars…</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {latencyRows.map((r) => {
            const tr = (r.translate_ms ?? 0) / maxEar;
            const tts = (r.tts_ms ?? 0) / maxEar;
            const ear = (r.ear_ms ?? 0) / maxEar;
            return (
              <div key={r.seg}>
                <div className="mono" style={{ fontSize: 12, marginBottom: 4 }}>
                  {r.seg} · {r.reason} · {r.ear_ms} ms total
                </div>
                <div style={{ display: "flex", height: 18, borderRadius: 4, overflow: "hidden", background: "var(--panel2)" }}>
                  <div
                    title={`translate ${r.translate_ms} ms`}
                    style={{ width: `${tr * 100}%`, background: "var(--xlate)" }}
                  />
                  <div
                    title={`tts ${r.tts_ms} ms`}
                    style={{ width: `${tts * 100}%`, background: "var(--me)" }}
                  />
                  <div
                    title={`ear-to-ear ${r.ear_ms} ms`}
                    style={{ width: `${Math.max(ear - tr - tts, 0.05) * 100}%`, background: "var(--them)", opacity: 0.5 }}
                  />
                </div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginTop: 2 }}>
                  {r.src} → {r.tgt}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {spoken.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 12, color: "var(--dim)" }}>
          {spoken.length} spoken segment{spoken.length === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}
