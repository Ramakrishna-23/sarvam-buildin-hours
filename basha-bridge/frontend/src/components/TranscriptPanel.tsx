import type { BusEvent } from "../types";

type Props = {
  events: BusEvent[];
  me?: string;
};

export function TranscriptPanel({ events, me }: Props) {
  const rows = events.filter((e) =>
    ["utterance.partial", "utterance.final", "segment.translated"].includes(e.tag)
  );

  return (
    <div className="panel" style={{ height: "100%" }}>
      <h2>Transcript stream</h2>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 360, overflow: "auto" }}>
        {rows.length === 0 && <div style={{ color: "var(--dim)" }}>Waiting for speech…</div>}
        {rows.map((e, i) => {
          const speaker = String(e.speaker ?? "fixture");
          const isMe = me ? speaker === me : false;
          const partial = e.tag === "utterance.partial";
          const xlate = e.tag === "segment.translated";
          const text =
            e.tag === "segment.translated"
              ? String(e.tgt ?? "")
              : String(e.text ?? "");
          return (
            <div
              key={i}
              style={{
                alignSelf: xlate ? "flex-start" : isMe ? "flex-end" : "flex-start",
                maxWidth: "85%",
                padding: "8px 12px",
                borderRadius: 10,
                background: xlate ? "#171126" : "var(--panel2)",
                borderLeft: xlate
                  ? "3px solid var(--xlate)"
                  : isMe
                    ? "none"
                    : "3px solid var(--them)",
                borderRight: isMe ? "3px solid var(--me)" : "none",
                opacity: partial ? 0.55 : 1,
                fontStyle: partial ? "italic" : "normal",
              }}
            >
              <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 2 }}>
                {xlate ? "interpreter" : speaker}
                {e.lang ? ` · ${String(e.lang)}` : ""}
                {e.reason ? ` · commit:${String(e.reason)}` : ""}
              </div>
              <div>{text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
