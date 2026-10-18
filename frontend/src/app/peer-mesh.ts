/**
 * Participant voice: one WebRTC audio connection per pair of participants (a mesh), signalled over the meeting
 * WebSocket. Audio flows browser to browser, so Chrome's echo canceller removes other people's voices from each
 * mic, and Tensor's transcription keeps every speaker separate.
 *
 * Exactly one side offers: the participant whose name sorts first. An offer from a peer always starts a fresh
 * connection (they reloaded or reconnected).
 */
const ICE_SERVERS: RTCIceServer[] = [{ urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] }];

export type PeerState = 'connecting' | 'connected' | 'failed';

interface Peer {
  pc: RTCPeerConnection;
  audio: HTMLAudioElement;
  pending: RTCIceCandidateInit[];
  retryTimer?: ReturnType<typeof setTimeout>;
}

export class PeerMesh {
  private peers = new Map<string, Peer>();
  private track: MediaStreamTrack | null = null;
  private muted = false;

  constructor(
    private me: string,
    private signal: (to: string, data: object) => void,
    private onState: (name: string, state: PeerState | null) => void,
    private onAutoplayBlocked: () => void,
  ) {}

  /** Keep one connection per connected participant. */
  sync(names: string[]) {
    const wanted = new Set(names.filter((n) => n !== this.me));
    for (const name of [...this.peers.keys()]) if (!wanted.has(name)) this.close(name);
    for (const name of wanted) if (!this.peers.has(name) && this.me < name) void this.offer(name);
  }

  /** The local mic track (null when the mic is off): swapped in without renegotiating. */
  setTrack(track: MediaStreamTrack | null) {
    this.track = track;
    for (const peer of this.peers.values()) void this.attach(peer);
  }

  /** Mute everyone else on this device (e.g. a second laptop in the same room). */
  setMuted(muted: boolean) {
    this.muted = muted;
    for (const peer of this.peers.values()) peer.audio.muted = muted;
  }

  /** Retry playback after a click when the browser blocked autoplay. */
  resume() {
    for (const peer of this.peers.values()) void peer.audio.play().catch(() => undefined);
  }

  async onSignal(from: string, data: { description?: RTCSessionDescriptionInit; candidate?: RTCIceCandidateInit }) {
    if (data.description?.type === 'offer') {
      this.close(from); // a new offer means a new session from that participant
      const peer = this.create(from);
      await peer.pc.setRemoteDescription(data.description);
      for (const t of peer.pc.getTransceivers()) t.direction = 'sendrecv';
      await this.attach(peer);
      await peer.pc.setLocalDescription(await peer.pc.createAnswer());
      this.signal(from, { description: peer.pc.localDescription });
      await this.flush(peer);
      return;
    }
    const peer = this.peers.get(from);
    if (!peer) return;
    if (data.description?.type === 'answer' && peer.pc.signalingState === 'have-local-offer') {
      await peer.pc.setRemoteDescription(data.description);
      await this.flush(peer);
    } else if (data.candidate) {
      if (peer.pc.remoteDescription) await peer.pc.addIceCandidate(data.candidate).catch(() => undefined);
      else peer.pending.push(data.candidate);
    }
  }

  closeAll() {
    for (const name of [...this.peers.keys()]) this.close(name);
  }

  private async offer(name: string) {
    const peer = this.create(name);
    peer.pc.addTransceiver('audio', { direction: 'sendrecv' });
    await this.attach(peer);
    await peer.pc.setLocalDescription(await peer.pc.createOffer());
    this.signal(name, { description: peer.pc.localDescription });
  }

  private create(name: string): Peer {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    const audio = new Audio();
    audio.autoplay = true;
    audio.muted = this.muted;
    const peer: Peer = { pc, audio, pending: [] };
    this.peers.set(name, peer);
    this.onState(name, 'connecting');
    pc.onicecandidate = (e) => {
      if (e.candidate) this.signal(name, { candidate: e.candidate.toJSON() });
    };
    pc.ontrack = (e) => {
      audio.srcObject = e.streams[0] ?? new MediaStream([e.track]);
      audio.play().catch(() => this.onAutoplayBlocked());
    };
    pc.onconnectionstatechange = () => {
      if (this.peers.get(name) !== peer) return;
      const state = pc.connectionState;
      if (state === 'connected') this.onState(name, 'connected');
      else if (state === 'failed') {
        this.onState(name, 'failed');
        // The offering side tries again with a fresh connection.
        if (this.me < name) peer.retryTimer = setTimeout(() => this.peers.get(name) === peer && this.restart(name), 4000);
      }
    };
    return peer;
  }

  private restart(name: string) {
    this.close(name);
    void this.offer(name);
  }

  private async attach(peer: Peer) {
    const sender = peer.pc.getTransceivers()[0]?.sender;
    if (sender) await sender.replaceTrack(this.track).catch(() => undefined);
  }

  private async flush(peer: Peer) {
    for (const c of peer.pending.splice(0)) await peer.pc.addIceCandidate(c).catch(() => undefined);
  }

  private close(name: string) {
    const peer = this.peers.get(name);
    if (!peer) return;
    clearTimeout(peer.retryTimer);
    this.peers.delete(name);
    peer.pc.close();
    peer.audio.srcObject = null;
    this.onState(name, null);
  }
}
