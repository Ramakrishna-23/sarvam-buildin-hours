// Dev-only token server. Issues LiveKit access tokens so the two web clients
// (rider/driver) can be tested end-to-end before the real backend/orchestrator
// exists. Not for production use — the orchestrator should own token issuance
// once it's built, since it also needs to control room/participant metadata
// for the agent.
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import express from 'express'
import cors from 'cors'
import { AccessToken } from 'livekit-server-sdk'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
dotenv.config({ path: path.join(__dirname, '.env'), quiet: true })

const PORT = process.env.PORT ?? 8787
const LIVEKIT_URL = process.env.LIVEKIT_URL
const API_KEY = process.env.LIVEKIT_API_KEY
const API_SECRET = process.env.LIVEKIT_API_SECRET

if (!LIVEKIT_URL || !API_KEY || !API_SECRET) {
  console.error(
    'Missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET.\n' +
      'Copy server/.env.example to server/.env and fill in your LiveKit Cloud project values.',
  )
  process.exit(1)
}

// Loopback-only binding stops other devices from reaching this server, but a
// browser tab on any website (or another local process/server) can still
// make a cross-origin fetch to localhost — allowing any localhost port isn't
// enough, since any other local server (a second dev tool, or something
// malicious) can also serve from a localhost port. Only the exact known
// client origin gets access to the response.
const CLIENT_ORIGIN = process.env.CLIENT_ORIGIN ?? 'http://localhost:5173'

const app = express()
app.use(cors({ origin: CLIENT_ORIGIN }))
app.use(express.json())

app.post('/token', async (req, res) => {
  const { room, identity, role } = req.body ?? {}
  if (!room || !identity) {
    return res.status(400).json({ error: 'room and identity are required' })
  }

  const at = new AccessToken(API_KEY, API_SECRET, {
    identity,
    ttl: '1h',
    metadata: JSON.stringify({ role: role ?? 'rider' }),
  })
  at.addGrant({
    room,
    roomJoin: true,
    canPublish: true,
    canSubscribe: true,
  })

  const token = await at.toJwt()
  res.json({ token, serverUrl: LIVEKIT_URL, identity })
})

// Loopback-only: this issues valid room-join credentials for any room/identity
// with no auth check, so it must never be reachable from other devices on the
// network. Real token issuance belongs on the orchestrator once it exists.
app.listen(PORT, '127.0.0.1', () => {
  console.log(`Dev token server listening on http://localhost:${PORT} (loopback only)`)
})
