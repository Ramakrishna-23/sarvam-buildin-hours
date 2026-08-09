import { useMemo } from "react";
import { LatencyTable } from "../components/LatencyTable";
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

export function LiveDemo() {
  const params = new URLSearchParams(location.search);
  const room = params.get("room") || "demo";
  const identity = params.get("id") || "rider";
  const other = identity === "rider" ? "driver" : "rider";

  const { events, push } = useBusEvents();
  const { status, error, micLevel, ttsActive, join } = useLiveKitRoom(identity, room, push);

  const pairLocked = events.find((e) => e.tag === "pair.locked");
  const latencyRows = useMemo(() => latencyFromEvents(events), [events]);
  const lastEar = [...events].reverse().find((e) => e.tag === "segment.spoken")?.ear_ms as
    | number
    | undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div
        style={{
          padding: "10px 18px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          gap: 10,
          alignItems: "center",
        }}
      >
        <span className="badge">
          {room} · {identity}
        </span>
        <span className={`badge ${pairLocked ? "on" : ""}`}>
          {pairLocked
            ? Object.entries(pairLocked.pair as Record<string, string>)
                .map(([k, v]) => `${k}:${v}`)
                .join(" ⇄ ")
            : "detecting languages…"}
        </span>
        {lastEar != null && (
          <span className="mono" style={{ marginLeft: "auto", color: "var(--xlate)" }}>
            ear-to-ear {lastEar} ms
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        <TranscriptPanel events={events} me={identity} />
      </div>

      <div style={{ padding: "12px 18px", borderTop: "1px solid var(--border)" }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 10 }}>
          <button onClick={join} disabled={status !== "idle"}>
            {status === "live" ? "Live ●" : "Join & speak"}
          </button>
          <span style={{ color: "var(--dim)", fontSize: 12 }}>
            share: {location.origin}/?room={room}&amp;id={other}
          </span>
        </div>
        <VoiceMeter level={micLevel} ttsActive={ttsActive} label="Microphone" />
        {error && <div style={{ color: "var(--err)", fontSize: 13, marginTop: 8 }}>{error}</div>}
        {latencyRows.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <LatencyTable rows={latencyRows} />
          </div>
        )}
      </div>
    </div>
  );
}
