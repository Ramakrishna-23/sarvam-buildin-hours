import {
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
} from 'livekit-client';
import './style.css';

const form = document.getElementById('join-form');
const roomInput = document.getElementById('room');
const roleInput = document.getElementById('role');
const tokenEndpointInput = document.getElementById('token-endpoint');
const statusEl = document.getElementById('status');
const remoteEl = document.getElementById('remote');
const localEl = document.getElementById('local');
const logEl = document.getElementById('log');

const defaultRoom = import.meta.env.VITE_DEFAULT_ROOM || 'basha-demo';
const defaultTokenEndpoint = import.meta.env.VITE_TOKEN_ENDPOINT || 'http://127.0.0.1:8787/token';
const RELAY_GATE_TOPIC = 'relay_gate';

roomInput.value = defaultRoom;
tokenEndpointInput.value = defaultTokenEndpoint;

const params = new URLSearchParams(window.location.search);
if (params.get('room')) roomInput.value = params.get('room');
if (params.get('role')) roleInput.value = params.get('role');
if (params.get('tokenEndpoint')) tokenEndpointInput.value = params.get('tokenEndpoint');

let room;
let role;
let identity;
let relayMode = 'detecting';
let roleTracks = {
  customer: 'agent-to-customer',
  driver: 'agent-to-driver',
};

function log(message) {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  logEl.textContent = `${line}\n${logEl.textContent}`;
  console.log(line);
}

function roleFromParticipant(participant) {
  try {
    const metadata = participant.metadata ? JSON.parse(participant.metadata) : {};
    if (metadata.role) return metadata.role;
  } catch (_) {
    // ignore invalid metadata
  }
  if (participant.identity === 'relay-agent') return 'agent';
  if (participant.identity.includes('driver')) return 'driver';
  if (participant.identity.includes('customer')) return 'customer';
  return 'unknown';
}

function expectedAgentTracksForRole(currentRole) {
  // Phase 4 uses role-target tracks. Keep Phase 3 language-track names as a
  // compatibility fallback while Railway rolls forward. Observer attaches both
  // directions for deterministic fixture testing.
  if (currentRole === 'customer') return [roleTracks.customer, 'agent-hi'];
  if (currentRole === 'driver') return [roleTracks.driver, 'agent-kn'];
  if (currentRole === 'observer') {
    return [roleTracks.customer, roleTracks.driver, 'agent-hi', 'agent-kn'];
  }
  return [];
}

function trackNameFor(track, publication) {
  return publication.trackName || publication.name || track.name || '';
}

function shouldAttachAudio(track, publication, participant) {
  const participantRole = roleFromParticipant(participant);
  const trackName = trackNameFor(track, publication);

  if (participantRole === 'agent') {
    return expectedAgentTracksForRole(role).some((expectedTrack) => (
      expectedTrack && trackName.includes(expectedTrack)
    ));
  }

  // Do not attach own remote echoes.
  if (participant.identity === identity) return false;

  // Humans hear each other normally while the agent is detecting or silent;
  // duck only after the mismatch relay gate opens.
  return true;
}

function volumeForAudio(publication, participant) {
  const participantRole = roleFromParticipant(participant);
  const trackName = publication.trackName || publication.name || '';
  if (participantRole === 'agent' && expectedAgentTracksForRole(role).some((t) => t && trackName.includes(t))) {
    return 1.0;
  }
  if (participantRole === 'driver' || participantRole === 'customer') {
    if (role === 'observer') return 0.05;
    return relayMode === 'relay' ? 0.08 : 1.0;
  }
  return 1.0;
}

function updateAudioVolumes() {
  remoteEl.querySelectorAll('audio[data-participant-role]').forEach((el) => {
    const participantRole = el.dataset.participantRole;
    if (participantRole === 'driver' || participantRole === 'customer') {
      el.volume = role === 'observer' ? 0.05 : (relayMode === 'relay' ? 0.08 : 1.0);
    } else if (participantRole === 'agent') {
      el.volume = 1.0;
    }
  });
}

function attachAudio(track, publication, participant) {
  if (track.kind !== Track.Kind.Audio) return;
  if (!shouldAttachAudio(track, publication, participant)) {
    log(`ignoring audio track ${publication.trackName || publication.name || track.sid} from ${participant.identity}`);
    return;
  }

  const participantRole = roleFromParticipant(participant);
  const wrapper = document.createElement('div');
  wrapper.className = 'track-card';
  const title = document.createElement('div');
  title.textContent = `${participant.identity} / ${publication.trackName || publication.name || track.name || 'audio'}`;
  wrapper.appendChild(title);

  const el = track.attach();
  el.autoplay = true;
  el.controls = true;
  el.dataset.participantRole = participantRole;
  el.dataset.trackName = trackNameFor(track, publication);
  el.volume = volumeForAudio(publication, participant);
  wrapper.appendChild(el);
  remoteEl.appendChild(wrapper);
  log(`attached ${title.textContent} volume=${el.volume}`);
}

function detachAudio(track) {
  track.detach().forEach((el) => {
    el.parentElement?.remove();
    el.remove();
  });
}

function handleRelayGate(payload, participant, _kind, topic) {
  if (topic && topic !== RELAY_GATE_TOPIC) return;
  if (participant?.identity !== 'relay-agent') return;

  try {
    const text = new TextDecoder().decode(payload);
    const event = JSON.parse(text);
    if (event.type !== 'relay_gate') return;
    relayMode = event.mode || 'detecting';
    if (event.tracks) roleTracks = { ...roleTracks, ...event.tracks };
    updateAudioVolumes();

    if (relayMode === 'relay') {
      log(`relay gate opened: ${JSON.stringify(event.languages)}; original audio ducked`);
    } else if (relayMode === 'silent') {
      log(`same-language call detected: ${JSON.stringify(event.languages)}; agent stays silent`);
    } else {
      log(`relay gate state: ${relayMode}`);
    }
  } catch (err) {
    log(`ignored malformed relay gate event: ${err.message}`);
  }
}

async function getToken({ endpoint, roomName, role }) {
  const url = new URL(endpoint);
  url.searchParams.set('room', roomName);
  url.searchParams.set('role', role);
  url.searchParams.set('identity', role);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`token request failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function join(event) {
  event.preventDefault();
  if (room) {
    await room.disconnect();
    remoteEl.innerHTML = '';
    localEl.innerHTML = '';
  }

  relayMode = 'detecting';
  role = roleInput.value;
  const roomName = roomInput.value.trim() || 'basha-demo';
  const endpoint = tokenEndpointInput.value.trim();
  statusEl.textContent = 'fetching token...';
  const tokenData = await getToken({ endpoint, roomName, role });
  identity = tokenData.identity;

  room = new Room({
    adaptiveStream: true,
    dynacast: true,
  });

  room.on(RoomEvent.TrackSubscribed, attachAudio);
  room.on(RoomEvent.TrackUnsubscribed, detachAudio);
  room.on(RoomEvent.DataReceived, handleRelayGate);
  room.on(RoomEvent.ParticipantConnected, (participant) => log(`participant connected: ${participant.identity}`));
  room.on(RoomEvent.ParticipantDisconnected, (participant) => log(`participant disconnected: ${participant.identity}`));
  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    statusEl.textContent = `${role} / ${roomName} / ${state}`;
    log(`connection: ${state}`);
  });

  statusEl.textContent = 'connecting...';
  await room.connect(tokenData.url, tokenData.token);
  log(`connected as ${identity}; waiting for language lock`);

  // Observer is listen-only for deterministic fixture testing.
  if (role === 'observer') {
    localEl.textContent = 'observer is listen-only';
    log('observer mode: no microphone published');
  } else {
    // Phase 4 starts as normal human audio. The agent silently detects language,
    // then either opens relay and ducks originals or stays silent for same-language.
    const mic = await createLocalAudioTrack({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
    });
    await room.localParticipant.publishTrack(mic, { name: 'mic' });
    const localAudio = mic.attach();
    localAudio.muted = true;
    localAudio.controls = true;
    localEl.appendChild(localAudio);
    log('published microphone; speak two short utterances to lock language');
  }

  // Attach any already-subscribed remote tracks.
  room.remoteParticipants.forEach((participant) => {
    participant.trackPublications.forEach((publication) => {
      if (publication.track) attachAudio(publication.track, publication, participant);
    });
  });
}

form.addEventListener('submit', (event) => {
  join(event).catch((err) => {
    console.error(err);
    statusEl.textContent = `error: ${err.message}`;
    log(`ERROR ${err.stack || err.message}`);
  });
});
