import { useCallback, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  Track,
  type LocalAudioTrack,
  type RemoteTrack,
} from "livekit-client";
import type { BusEvent } from "../types";

type Status = "idle" | "connecting" | "live" | "error";

export function useLiveKitRoom(identity: string, roomName: string, onEvent: (e: BusEvent) => void) {
  const roomRef = useRef<Room | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [ttsActive, setTtsActive] = useState(false);

  const join = useCallback(async () => {
    setStatus("connecting");
    setError(null);
    try {
      const res = await fetch(`/api/token?room=${roomName}&identity=${identity}`);
      const { url, token } = await res.json();
      const room = new Room({ adaptiveStream: true });
      roomRef.current = room;

      room.on(RoomEvent.DataReceived, (payload) => {
        try {
          const evt = JSON.parse(new TextDecoder().decode(payload)) as BusEvent;
          onEvent(evt);
          if (evt.tag === "tts.active") {
            setTtsActive(Boolean(evt.active) && evt.target === identity);
          }
        } catch {
          /* ignore */
        }
      });

      room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, pub, participant) => {
        if (track.kind !== Track.Kind.Audio) return;
        if (participant.identity === "agent") {
          if (pub.trackName === `xlate-to-${identity}`) track.attach();
          return;
        }
        track.attach();
      });

      room.on(RoomEvent.LocalTrackPublished, (pub) => {
        const track = pub.track as LocalAudioTrack | undefined;
        if (!track) return;
        const ctx = new AudioContext();
        const src = ctx.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        src.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteFrequencyData(data);
          const avg = data.reduce((a, b) => a + b, 0) / data.length;
          setMicLevel(avg / 255);
          if (roomRef.current?.state === "connected") requestAnimationFrame(tick);
        };
        tick();
      });

      await room.connect(url, token);
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
      });
      setStatus("live");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [identity, roomName, onEvent]);

  const leave = useCallback(async () => {
    await roomRef.current?.disconnect();
    roomRef.current = null;
    setStatus("idle");
    setMicLevel(0);
    setTtsActive(false);
  }, []);

  return { status, error, micLevel, ttsActive, join, leave };
}
