import {
  LiveKitRoom,
  RoomAudioRenderer,
  StartAudio,
  useParticipants,
  useLocalParticipant,
  useConnectionState,
} from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import type { Session } from './App'

export default function CallView({ session, onLeave }: { session: Session; onLeave: () => void }) {
  return (
    <LiveKitRoom
      token={session.token}
      serverUrl={session.serverUrl}
      connect
      audio
      video={false}
      onDisconnected={onLeave}
      data-lk-theme="default"
    >
      <RoomAudioRenderer />
      <StartAudio label="Click to enable audio" className="start-audio-button" />
      <CallUI session={session} onLeave={onLeave} />
    </LiveKitRoom>
  )
}

function CallUI({ session, onLeave }: { session: Session; onLeave: () => void }) {
  const participants = useParticipants()
  const { localParticipant, isMicrophoneEnabled } = useLocalParticipant()
  const connectionState = useConnectionState()

  const statusLabel =
    connectionState === ConnectionState.Connected
      ? 'Connected'
      : connectionState === ConnectionState.Connecting
        ? 'Connecting…'
        : connectionState === ConnectionState.Reconnecting
          ? 'Reconnecting…'
          : 'Disconnected'

  return (
    <div className="call-view">
      <header>
        <div>
          <h2>Room: {session.room}</h2>
          <span className={`status status-${connectionState}`}>{statusLabel}</span>
        </div>
        <span className="self-badge">{session.role === 'rider' ? 'Rider' : 'Driver'} · {session.identity}</span>
      </header>

      <ul className="participant-list">
        {participants.map((p) => (
          <li key={p.identity} className={p.isLocal ? 'local' : ''}>
            <span className="dot" data-speaking={p.isSpeaking} />
            {p.identity}
            {p.isLocal && ' (you)'}
          </li>
        ))}
        {participants.length <= 1 && (
          <li className="waiting">Waiting for the other participant to join…</li>
        )}
      </ul>

      <div className="controls">
        <button
          onClick={() => localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}
          className={isMicrophoneEnabled ? '' : 'muted'}
        >
          {isMicrophoneEnabled ? 'Mute' : 'Unmute'}
        </button>
        <button onClick={onLeave} className="leave-button">
          Leave
        </button>
      </div>
    </div>
  )
}
