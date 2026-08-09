import type { BusEvent } from "../types";

const LADDER = [
  "PASSIVE_MONITOR",
  "WATCH",
  "OFFER_HELP",
  "ACTIVE_MEDIATION",
  "RESOLVED",
] as const;

const STATE_COLOR: Record<string, string> = {
  PASSIVE_MONITOR: "var(--dim)",
  WATCH: "#ffb454",
  OFFER_HELP: "#59c2ff",
  ACTIVE_MEDIATION: "#d2a6ff",
  SAFETY_ESCALATION: "var(--err)",
  RESOLVED: "var(--ok)",
};

const REQUIRED = ["pickup_point", "driver_location", "eta", "agreed_next_action"];
const ALL_SLOTS = [
  "pickup_point",
  "landmark",
  "driver_location",
  "eta",
  "otp",
  "blocker",
  "agreed_next_action",
];

export function MediationPanel({ events }: { events: BusEvent[] }) {
  const stateEvents = events.filter((e) => e.tag === "agent.state");
  const last = stateEvents[stateEvents.length - 1];
  const state = String(last?.state ?? "PASSIVE_MONITOR");
  const score = Number(last?.score ?? 0);
  const signals = (last?.signals as string[]) ?? [];
  const evidence = (last?.evidence as string[]) ?? [];

  const lastSlots = [...events].reverse().find((e) => e.tag === "slots.updated");
  const slots = (lastSlots?.slots as Record<string, string>) ?? {};

  const actions = events.filter(
    (e) => e.tag === "mediation.action" || e.tag === "agent.speech"
  );
  const reachedIdx = LADDER.indexOf(state as (typeof LADDER)[number]);
  const safety = state === "SAFETY_ESCALATION";

  return (
    <div className="panel">
      <h2>Drift engine &amp; mediation</h2>

      {/* ladder */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 12 }}>
        {LADDER.map((s, i) => {
          const active = s === state;
          const passed = !safety && reachedIdx >= 0 && i <= reachedIdx;
          return (
            <div key={s} style={{ display: "flex", alignItems: "center", flex: 1 }}>
              <div
                title={s}
                style={{
                  flex: 1,
                  textAlign: "center",
                  fontSize: 10,
                  fontFamily: "var(--mono, ui-monospace, monospace)",
                  padding: "5px 2px",
                  borderRadius: 6,
                  background: active ? STATE_COLOR[s] : passed ? "#1d2536" : "transparent",
                  color: active ? "#0b0e14" : passed ? "var(--ink)" : "var(--dim)",
                  border: `1px solid ${active ? STATE_COLOR[s] : "#1d2536"}`,
                  fontWeight: active ? 700 : 400,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {s.replace("_", " ")}
              </div>
              {i < LADDER.length - 1 && (
                <span style={{ color: "var(--dim)", fontSize: 10, padding: "0 2px" }}>›</span>
              )}
            </div>
          );
        })}
      </div>

      {safety && (
        <div
          style={{
            background: "#2a0f12",
            border: "1px solid var(--err)",
            color: "var(--err)",
            borderRadius: 8,
            padding: "6px 10px",
            fontSize: 13,
            fontWeight: 700,
            marginBottom: 10,
          }}
        >
          ⚠ SAFETY ESCALATION
        </div>
      )}

      {/* score + signals */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: "var(--dim)" }}>drift score</span>
        <div
          style={{
            flex: 1,
            height: 8,
            borderRadius: 4,
            background: "#1d2536",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${Math.min((score / 6) * 100, 100)}%`,
              height: "100%",
              background: STATE_COLOR[state] ?? "var(--dim)",
              transition: "width .3s",
            }}
          />
        </div>
        <span
          style={{
            fontFamily: "var(--mono, ui-monospace, monospace)",
            fontSize: 13,
            color: STATE_COLOR[state],
            minWidth: 32,
          }}
        >
          {score.toFixed(1)}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 4 }}>
        {signals.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--dim)" }}>no signals</span>
        )}
        {signals.map((s) => (
          <span
            key={s}
            className="badge"
            style={{ fontSize: 10.5, color: s === "LANG_MISMATCH" ? "var(--dim)" : "var(--ink)" }}
          >
            {s}
          </span>
        ))}
      </div>
      {evidence.map((e, i) => (
        <div key={i} style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 2 }}>
          · {e}
        </div>
      ))}

      {/* slots */}
      <h3 style={{ fontSize: 12, color: "var(--dim)", margin: "14px 0 6px", letterSpacing: ".06em" }}>
        TASK SLOTS
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 3 }}>
        {ALL_SLOTS.map((name) => {
          const value = slots[name];
          const required = REQUIRED.includes(name);
          return (
            <div
              key={name}
              style={{
                display: "flex",
                gap: 8,
                fontSize: 12,
                padding: "3px 8px",
                borderRadius: 5,
                background: value ? "#0f2016" : "transparent",
                border: `1px solid ${value ? "#1e4028" : "#1d2536"}`,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--mono, ui-monospace, monospace)",
                  color: value ? "var(--ok)" : required ? "#ffb454" : "var(--dim)",
                  minWidth: 130,
                }}
              >
                {value ? "✓" : required ? "○" : "·"} {name}
              </span>
              <span style={{ color: value ? "var(--ink)" : "var(--dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {value ?? (required ? "required" : "—")}
              </span>
            </div>
          );
        })}
      </div>

      {/* agent utterances */}
      {actions.length > 0 && (
        <>
          <h3 style={{ fontSize: 12, color: "var(--dim)", margin: "14px 0 6px", letterSpacing: ".06em" }}>
            AGENT ACTIONS
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 160, overflowY: "auto" }}>
            {actions.map((a, i) => (
              <div
                key={i}
                style={{
                  fontSize: 12.5,
                  padding: "5px 9px",
                  borderRadius: 7,
                  background: "#171126",
                  borderLeft: "3px solid #d2a6ff",
                }}
              >
                {a.tag === "mediation.action" ? (
                  <>
                    <div style={{ fontSize: 10.5, color: "#d2a6ff", fontFamily: "var(--mono, ui-monospace, monospace)" }}>
                      {String(a.action)} → {String(a.target)}
                    </div>
                    <div>{String(a.utterance)}</div>
                    {a.reason ? (
                      <div style={{ fontSize: 11, color: "var(--dim)" }}>{String(a.reason)}</div>
                    ) : null}
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: 10.5, color: "#95e6cb", fontFamily: "var(--mono, ui-monospace, monospace)" }}>
                      spoken → {String(a.target)}
                    </div>
                    <div>{String(a.text)}</div>
                  </>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
