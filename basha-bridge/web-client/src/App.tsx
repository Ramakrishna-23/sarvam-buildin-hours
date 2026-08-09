import { useState } from 'react'
import JoinScreen from './JoinScreen'
import CallView from './CallView'
import './App.css'

export type Role = 'rider' | 'driver'

export interface Session {
  token: string
  serverUrl: string
  room: string
  identity: string
  role: Role
}

function App() {
  const [session, setSession] = useState<Session | null>(null)

  if (!session) {
    return <JoinScreen onJoined={setSession} />
  }

  return <CallView session={session} onLeave={() => setSession(null)} />
}

export default App
