import { useEffect, useState } from "react";

export type ScenarioInfo = {
  file: string;
  name: string;
  description: string;
  turns: number;
  expect: Record<string, unknown>;
};

export function ScenarioPanel({
  onRun,
  running,
  result,
}: {
  onRun: (file: string, llm: boolean, mediate: boolean) => void;
  running: boolean;
  result: { passed?: boolean; final_state?: string; path?: string[]; failures?: string[] } | null;
}) {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [llm, setLlm] = useState(false);
  const [mediate, setMediate] = useState(false);

  useEffect(() => {
    fetch("/api/scenarios")
      .then((r) => r.json())
      .then((s: ScenarioInfo[]) => {
        setScenarios(s);
        if (s.length && !selected) setSelected(s[0].file);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = scenarios.find((s) => s.file === selected);

  return (
    <div className="panel">
      <h2>Drift scenarios (Phase 5/6)</h2>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={running}
          style={{
            background: "#0b0e14",
            color: "var(--ink)",
            border: "1px solid #1d2536",
            borderRadius: 8,
            padding: "7px 10px",
            fontSize: 13,
            flex: 1,
            minWidth: 180,
          }}
        >
          {scenarios.map((s) => (
            <option key={s.file} value={s.file}>
              {s.name} ({s.turns} turns)
            </option>
          ))}
        </select>
        <button onClick={() => onRun(selected, llm, mediate)} disabled={running || !selected}>
          {running ? "Running…" : "Replay"}
        </button>
      </div>

      <div style={{ display: "flex", gap: 14, marginTop: 9, fontSize: 12.5, color: "var(--dim)" }}>
        <label style={{ display: "flex", gap: 5, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} disabled={running} />
          use sarvam-105b
        </label>
        <label
          style={{
            display: "flex",
            gap: 5,
            alignItems: "center",
            cursor: llm ? "pointer" : "not-allowed",
            opacity: llm ? 1 : 0.45,
          }}
        >
          <input
            type="checkbox"
            checked={mediate}
            onChange={(e) => setMediate(e.target.checked)}
            disabled={running || !llm}
          />
          run mediation
        </label>
      </div>

      {current && (
        <p style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--dim)" }}>{current.description}</p>
      )}

      {result && (
        <div
          style={{
            marginTop: 10,
            padding: "7px 10px",
            borderRadius: 8,
            fontSize: 12.5,
            background: result.passed ? "#0f2016" : "#2a0f12",
            border: `1px solid ${result.passed ? "var(--ok)" : "var(--err)"}`,
          }}
        >
          <b style={{ color: result.passed ? "var(--ok)" : "var(--err)" }}>
            {result.passed ? "✔ PASS" : "✘ FAIL"}
          </b>{" "}
          <span style={{ fontFamily: "var(--mono, ui-monospace, monospace)" }}>
            {(result.path ?? []).join(" → ")}
          </span>
          {(result.failures ?? []).map((f, i) => (
            <div key={i} style={{ color: "var(--err)", marginTop: 3 }}>
              - {f}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
