import type { BusEvent } from "../types";

const TAG_COLORS: Record<string, string> = {
  "utterance.partial": "var(--dim)",
  "utterance.final": "var(--them)",
  "segment.translated": "var(--xlate)",
  "segment.spoken": "var(--ok)",
  "pair.locked": "var(--ok)",
  "run.start": "var(--them)",
  "run.complete": "var(--ok)",
  "run.warn": "var(--warn)",
  "run.error": "var(--err)",
};

type Props = {
  events: BusEvent[];
  filter?: string;
};

export function EventLog({ events, filter }: Props) {
  const shown = filter
    ? events.filter((e) => e.tag.includes(filter))
    : events;

  return (
    <div className="panel" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <h2>Event bus ({shown.length})</h2>
      <div
        className="mono"
        style={{
          flex: 1,
          overflow: "auto",
          fontSize: 12,
          lineHeight: 1.45,
          maxHeight: 420,
        }}
      >
        {shown.length === 0 && <div style={{ color: "var(--dim)" }}>No events yet…</div>}
        {shown.map((e, i) => (
          <div key={i} style={{ marginBottom: 6, borderBottom: "1px solid var(--border)", paddingBottom: 4 }}>
            <span style={{ color: "var(--dim)" }}>+{e.ts ?? 0}ms </span>
            <span style={{ color: TAG_COLORS[e.tag] ?? "var(--ink)", fontWeight: 700 }}>
              {e.tag}
            </span>
            <pre style={{ margin: "2px 0 0", whiteSpace: "pre-wrap", color: "var(--ink)" }}>
              {JSON.stringify(
                Object.fromEntries(Object.entries(e).filter(([k]) => k !== "tag" && k !== "ts")),
                null,
                0
              )}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}
