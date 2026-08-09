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

roomInput.value = defaultRoom;
tokenEndpointInput.value = defaultTokenEndpoint;

const params = new URLSearchParams(window.location.search);
if (params.get('room')) roomInput.value = params.get('room');
if (params.get('role')) roleInput.value = params.get('role');
if (params.get('tokenEndpoint')) tokenEndpointInput.value = params.get('tokenEndpoint');

let room;
let role;
let identity;

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

function expectedAgentTrackForRole(currentRole) {
  if (currentRole === 'customer') return 'agent-hi';
  if (currentRole === 'driver') return 'agent-kn';
  return '';
}

function shouldAttachAudio(track, publication, participant) {
  const participantRole = roleFromParticipant(participant);
  const trackName = publication.trackName || publication.name || track.name || '';

  // Phase 3 publishes two agent tracks. Each side only attaches the translated
  // target-language track intended for that role.
  if (participantRole === 'agent') {
    const expectedTrack = expectedAgentTrackForRole(role);
    return Boolean(expectedTrack && trackName.includes(expectedTrack));
  }

  // Do not attach own remote echoes.
  if (participant.identity === identity) return false;

  // Humans hear each other with low gain underneath the translated relay.
  return true;
}

function volumeForAudio(publication, participant) {
  const participantRole = roleFromParticipant(participant);
  const trackName = publication.trackName || publication.name || '';
  const expectedTrack = expectedAgentTrackForRole(role);
  if (participantRole === 'agent' && expectedTrack && trackName.includes(expectedTrack)) return 1.0;
  if (participantRole === 'driver' || participantRole === 'customer') return 0.08;
  return 1.0;
}

function attachAudio(track, publication, participant) {
  if (track.kind !== Track.Kind.Audio) return;
  if (!shouldAttachAudio(track, publication, participant)) {
    log(`ignoring audio track ${publication.trackName || publication.name || track.sid} from ${participant.identity}`);
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'track-card';
  const title = document.createElement('div');
  title.textContent = `${participant.identity} / ${publication.trackName || publication.name || track.name || 'audio'}`;
  wrapper.appendChild(title);

  const el = track.attach();
  el.autoplay = true;
  el.controls = true;
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
  room.on(RoomEvent.ParticipantConnected, (participant) => log(`participant connected: ${participant.identity}`));
  room.on(RoomEvent.ParticipantDisconnected, (participant) => log(`participant disconnected: ${participant.identity}`));
  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    statusEl.textContent = `${role} / ${roomName} / ${state}`;
    log(`connection: ${state}`);
  });

  statusEl.textContent = 'connecting...';
  await room.connect(tokenData.url, tokenData.token);
  log(`connected as ${identity}`);

  // Phase 3 is full-duplex: both humans publish mic audio and the agent runs
  // independent relay pipelines in both directions. Headphones are strongly
  // recommended during testing to avoid acoustic feedback into the microphone.
  const mic = await createLocalAudioTrack({
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  });
  await room.localParticipant.publishTrack(mic, { name: 'mic' });
  const localAudio = mic.attach();
  localAudio.muted = true;
  localAudio.controls = true;
  localEl.appendChild(localAudio);
  log('published microphone; use headphones for Phase 3 full-duplex testing');

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
