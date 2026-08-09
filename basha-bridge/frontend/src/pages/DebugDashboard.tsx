import { useMemo, useState } from "react";
import { EventLog } from "../components/EventLog";
import { FixturePanel } from "../components/FixturePanel";
import { LatencyTable } from "../components/LatencyTable";
import { MediationPanel } from "../components/MediationPanel";
import { PipelineTimeline } from "../components/PipelineTimeline";
import { ScenarioPanel } from "../components/ScenarioPanel";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { VoiceMeter } from "../components/VoiceMeter";
import { useBusEvents } from "../hooks/useBusEvents";
import { useLiveKitRoom } from "../hooks/useLiveKitRoom";
import type { BusEvent, LatencyRow } from "../types";

function latencyFromEvents(events: BusEvent[]): LatencyRow[] {
  const translated = new Map(
    events.filter((e) => e.tag === "segment.translated").map((e) => [String(e.seg), e])
  );
  return events
    .filter((e) => e.tag === "segment.spoken")
    .map((e) => {
      const tr = translated.get(String(e.seg));
      return {
        seg: String(e.seg),
        reason: String(tr?.reason ?? "?"),
        src: String(tr?.src ?? ""),
        tgt: String(tr?.tgt ?? ""),
        translate_ms: tr?.translate_ms as number | undefined,
        tts_ms: e.tts_ms as number | undefined,
        ear_ms: e.ear_ms as number | undefined,
      };
    });
}

export function DebugDashboard() {
  const params = new URLSearchParams(location.search);
  const room = params.get("room") || "demo";
  const identity = params.get("id") || "rider";

  const { events, push, reset } = useBusEvents();
  const { status, error, micLevel, ttsActive, join, leave } = useLiveKitRoom(
    identity,
    room,
    push
  );
  const [fixtureRunning, setFixtureRunning] = useState(false);
  const [scenarioRunning, setScenarioRunning] = useState(false);
  const [scenarioResult, setScenarioResult] = useState<{
    passed?: boolean;
    final_state?: string;
    path?: string[];
    failures?: string[];
  } | null>(null);

  const latencyRows = useMemo(() => latencyFromEvents(events), [events]);
  const lastEar = [...events].reverse().find((e) => e.tag === "segment.spoken")?.ear_ms as
    | number
    | undefined;

  const consumeSse = async (url: string, onEvent: (evt: BusEvent) => void) => {
    const res = await fetch(url);
    const reader = res.body?.getReader();
    if (!reader) return;
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as BusEvent);
        } catch {
          /* ignore */
        }
      }
    }
  };

  const runFixture = async (fixture: string, tgt: string) => {
    reset();
    setFixtureRunning(true);
    try {
      await consumeSse(
        `/api/offline-run?fixture=${encodeURIComponent(fixture)}&tgt=${encodeURIComponent(tgt)}`,
        push
      );
    } finally {
      setFixtureRunning(false);
    }
  };

  const runScenario = async (file: string, llm: boolean, mediate: boolean) => {
    reset();
    setScenarioResult(null);
    setScenarioRunning(true);
    try {
      await consumeSse(
        `/api/scenario-run?scenario=${encodeURIComponent(file)}&llm=${llm ? 1 : 0}&mediate=${
          mediate ? 1 : 0
        }`,
        (evt) => {
          push(evt);
          if (evt.tag === "scenario.complete") {
            setScenarioResult({
              passed: Boolean(evt.passed),
              final_state: String(evt.final_state ?? ""),
              path: (evt.path as string[]) ?? [],
              failures: (evt.failures as string[]) ?? [],
            });
          }
        }
      );
    } finally {
      setScenarioRunning(false);
    }
  };

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14, flex: 1 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 14,
        }}
      >
        <FixturePanel onRun={runFixture} running={fixtureRunning} />
        <div className="panel">
          <h2>Live room ({room})</h2>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
            <span className="badge">{identity}</span>
            <span className="badge" style={{ color: status === "live" ? "var(--ok)" : "var(--dim)" }}>
              {status}
            </span>
            {status === "idle" && <button onClick={join}>Join &amp; speak</button>}
            {status === "live" && (
              <button className="secondary" onClick={leave}>
                Leave
              </button>
            )}
            <button className="secondary" onClick={reset}>
              Clear log
            </button>
          </div>
          {error && <div style={{ color: "var(--err)", fontSize: 13 }}>{error}</div>}
          <VoiceMeter level={micLevel} ttsActive={ttsActive} label="Mic / TTS duck" />
          <p style={{ margin: "10px 0 0", fontSize: 12, color: "var(--dim)" }}>
            Agent: <code>uv run basha-bridge agent --room {room}</code>
          </p>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <ScenarioPanel onRun={runScenario} running={scenarioRunning} result={scenarioResult} />
        <LatencyTable rows={latencyRows} lastEarMs={lastEar} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <TranscriptPanel events={events} me={identity} />
        <MediationPanel events={events} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <PipelineTimeline events={events} latencyRows={latencyRows} />
        <EventLog events={events} />
      </div>
    </div>
  );
}
