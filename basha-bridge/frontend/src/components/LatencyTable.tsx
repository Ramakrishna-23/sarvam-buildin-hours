import type { LatencyRow } from "../types";

type Props = {
  rows: LatencyRow[];
  lastEarMs?: number;
};

export function LatencyTable({ rows, lastEarMs }: Props) {
  return (
    <div className="panel">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>Latency</h2>
        {lastEarMs != null && (
          <span className="mono" style={{ color: "var(--xlate)", fontSize: 13 }}>
            ear-to-ear {lastEarMs} ms
          </span>
        )}
      </div>
      {rows.length === 0 ? (
        <div style={{ color: "var(--dim)", fontSize: 13 }}>Segments will appear as commits land…</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ color: "var(--dim)", textAlign: "left" }}>
              <th>seg</th>
              <th>reason</th>
              <th>translate</th>
              <th>tts</th>
              <th>ear-to-ear</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.seg} style={{ borderTop: "1px solid var(--border)" }}>
                <td className="mono">{r.seg}</td>
                <td>{r.reason}</td>
                <td className="mono">{r.translate_ms ?? "–"} ms</td>
                <td className="mono">{r.tts_ms ?? "–"} ms</td>
                <td className="mono" style={{ color: "var(--xlate)", fontWeight: 700 }}>
                  {r.ear_ms ?? "–"} ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
