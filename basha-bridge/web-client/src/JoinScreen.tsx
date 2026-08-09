import { useState } from 'react'
import type { Role, Session } from './App'

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT ?? 'http://localhost:8787/token'

function paramOrDefault(key: string, fallback: string) {
  const params = new URLSearchParams(window.location.search)
  return params.get(key) ?? fallback
}

// Decodes the JWT payload (no signature check — display/routing purposes only,
// LiveKit itself is what actually validates the token). Used so a manually
// pasted token's real identity/room/role are what we connect and display
// with, instead of whatever was separately typed/selected in the form above.
function decodeTokenClaims(token: string): { identity?: string; room?: string; role?: Role } | null {
  try {
    const payload = token.split('.')[1]
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const claims = JSON.parse(json)
    let role: Role | undefined
    if (typeof claims.metadata === 'string') {
      try {
        const meta = JSON.parse(claims.metadata)
        if (meta.role === 'rider' || meta.role === 'driver') role = meta.role
      } catch {
        // token metadata isn't JSON, or has no role — fall back to the form's selection
      }
    }
    return { identity: claims.identity, room: claims.video?.room, role }
  } catch {
    return null
  }
}

export default function JoinScreen({ onJoined }: { onJoined: (s: Session) => void }) {
  const [room, setRoom] = useState(paramOrDefault('room', 'demo-ride-1'))
  const [identity, setIdentity] = useState(paramOrDefault('identity', ''))
  const [role, setRole] = useState<Role>((paramOrDefault('role', 'rider') as Role) ?? 'rider')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [showManual, setShowManual] = useState(false)
  const [manualToken, setManualToken] = useState('')
  const [manualUrl, setManualUrl] = useState('')

  async function join(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      // Manual override: paste a token minted elsewhere (e.g. LiveKit dashboard)
      // instead of hitting the dev token server. Useful before the token
      // server has real API credentials, or to test against someone else's
      // already-issued token.
      if (manualToken && manualUrl) {
        const claims = decodeTokenClaims(manualToken)
        if (!claims?.identity || !claims.room) {
          throw new Error('Could not read identity/room from that token — check it was pasted in full.')
        }
        // The token's own claims are authoritative, not the room/name/role
        // fields above — connecting with mismatched displayed metadata would
        // make test results misleading. Role falls back to the form's
        // selection only when the token itself carries no role metadata
        // (e.g. one minted outside our token server).
        onJoined({
          token: manualToken,
          serverUrl: manualUrl,
          room: claims.room,
          identity: claims.identity,
          role: claims.role ?? role,
        })
        return
      }

      const res = await fetch(TOKEN_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room, identity: identity || `${role}-${Date.now()}`, role }),
      })
      if (!res.ok) {
        throw new Error(`Token server returned ${res.status}`)
      }
      const data = await res.json()
      onJoined({
        token: data.token,
        serverUrl: data.serverUrl,
        room,
        identity: identity || data.identity,
        role,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to join')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="join-screen">
      <h1>Basha Bridge</h1>
      <p className="subtitle">Join as rider or driver to test the pickup-coordination call.</p>
      <form onSubmit={join}>
        <label>
          Role
          <div className="role-toggle">
            <button
              type="button"
              className={role === 'rider' ? 'active' : ''}
              onClick={() => setRole('rider')}
            >
              Rider
            </button>
            <button
              type="button"
              className={role === 'driver' ? 'active' : ''}
              onClick={() => setRole('driver')}
            >
              Driver
            </button>
          </div>
        </label>

        <label>
          Room
          <input value={room} onChange={(e) => setRoom(e.target.value)} required />
        </label>

        <label>
          Name (optional)
          <input
            value={identity}
            onChange={(e) => setIdentity(e.target.value)}
            placeholder={`${role}-${Date.now()}`}
          />
        </label>

        <button type="button" className="advanced-toggle" onClick={() => setShowManual((v) => !v)}>
          {showManual ? 'Hide manual token' : 'Advanced: paste a token manually'}
        </button>

        {showManual && (
          <>
            <label>
              LiveKit server URL
              <input
                value={manualUrl}
                onChange={(e) => setManualUrl(e.target.value)}
                placeholder="wss://your-project.livekit.cloud"
              />
            </label>
            <label>
              Access token
              <textarea
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder="eyJhbGciOi..."
                rows={3}
              />
            </label>
          </>
        )}

        {error && <p className="error">{error}</p>}

        <button type="submit" disabled={loading} className="join-button">
          {loading ? 'Joining…' : 'Join call'}
        </button>
      </form>
      <p className="hint">
        Tip: open this page in two tabs — one with <code>?role=rider</code>, one with{' '}
        <code>?role=driver</code> — and use the same room name to test both sides.
      </p>
    </div>
  )
}
