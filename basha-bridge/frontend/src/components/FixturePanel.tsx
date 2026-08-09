import { useEffect, useState } from "react";
import type { FixtureInfo } from "../types";

type Props = {
  onRun: (fixture: string, tgt: string) => void;
  running: boolean;
};

export function FixturePanel({ onRun, running }: Props) {
  const [fixtures, setFixtures] = useState<FixtureInfo[]>([]);
  const [selected, setSelected] = useState("hi_otp.wav");
  const [tgt, setTgt] = useState("kn-IN");

  useEffect(() => {
    fetch("/api/fixtures")
      .then((r) => r.json())
      .then((list: FixtureInfo[]) => {
        setFixtures(list);
        const hiOtp = list.find((f) => f.name === "hi_otp.wav");
        if (hiOtp) {
          setSelected(hiOtp.name);
          setTgt(hiOtp.default_tgt);
        }
      })
      .catch(() => {});
  }, []);

  const current = fixtures.find((f) => f.name === selected);

  return (
    <div className="panel">
      <h2>Offline fixture replay</h2>
      <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--dim)", lineHeight: 1.5 }}>
        Streams a WAV at real-time pace through STT → chunker → Mayura → Bulbul (same pipeline as
        the live agent, no WebRTC).
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
        <select value={selected} onChange={(e) => {
          const name = e.target.value;
          setSelected(name);
          const f = fixtures.find((x) => x.name === name);
          if (f) setTgt(f.default_tgt);
        }}>
          {fixtures.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name} {!f.exists ? "(missing)" : ""}
            </option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
          target
          <select value={tgt} onChange={(e) => setTgt(e.target.value)}>
            <option value="kn-IN">kn-IN</option>
            <option value="hi-IN">hi-IN</option>
          </select>
        </label>
        <button onClick={() => onRun(selected, tgt)} disabled={running || !current?.exists}>
          {running ? "Running…" : "Run fixture"}
        </button>
      </div>
      {current && (
        <div
          style={{
            fontSize: 13,
            background: "var(--panel2)",
            borderRadius: 8,
            padding: 12,
            lineHeight: 1.55,
          }}
        >
          <div>
            <strong>Input text</strong> ({current.lang} · {current.speaker})
          </div>
          <div style={{ margin: "6px 0" }}>{current.text}</div>
          <div style={{ color: "var(--dim)" }}>{current.notes}</div>
        </div>
      )}
    </div>
  );
}
