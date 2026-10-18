import { Injectable, computed, inject, signal } from '@angular/core';
import { Auth } from './auth.service';
import { hasWakeWord } from './wake-word';
import { PeerMesh, type PeerState } from './peer-mesh';
import { VoicePlayer } from './voice-player';
import type {
  ActionCard,
  ChatMessage,
  MeetingDocument,
  Participant,
  Segment,
  Snapshot,
  TensorState,
} from './models';

const VAD_THRESHOLD = 0.018; // RMS of a 100 ms frame
const VAD_HANGOVER_MS = 700;

/** One meeting room: WebSocket state sync, microphone streaming, Tensor's voice playback. */
@Injectable({ providedIn: 'root' })
export class Room {
  private auth = inject(Auth);
  readonly meetingId = signal('');
  readonly me = signal('');
  readonly connection = signal<'offline' | 'connecting' | 'live' | 'reconnecting'>('offline');
  readonly micOn = signal(false);
  readonly micError = signal('');
  readonly localSpeaking = signal(false);
  readonly tensor = signal<TensorState>({ status: 'idle' });
  readonly participants = signal<Participant[]>([]);
  readonly transcript = signal<Segment[]>([]);
  readonly interim = signal<Record<string, string>>({});
  readonly chat = signal<ChatMessage[]>([]);
  readonly documents = signal<MeetingDocument[]>([]);
  readonly cards = signal<ActionCard[]>([]);
  readonly caption = signal<string>('');
  readonly error = signal('');
  /** Play Tensor's voice on this device (turn off on extra laptops in the same physical room). */
  readonly voiceOn = signal(true);
  readonly wakeHintAvailable = signal(false);
  /** Voice links to other participants, and what they're doing (from their browsers via the server). */
  readonly peerStates = signal<Record<string, PeerState>>({});
  readonly presence = signal<Record<string, { speaking?: boolean; mic?: boolean }>>({});
  readonly audioBlocked = signal(false);
  /** Set when an admin ends the meeting for everyone. */
  readonly ended = signal<{ by: string } | null>(null);

  readonly openCards = computed(() =>
    this.cards().filter((c) => !['CANCELLED'].includes(c.status)),
  );
  readonly pendingCount = computed(
    () =>
      this.cards().filter((c) => c.status === 'PENDING_REVIEW' || c.status === 'MODIFIED').length,
  );

  private ws?: WebSocket;
  private audioContext?: AudioContext;
  private stream?: MediaStream;
  private worklet?: AudioWorkletNode;
  private lastVoice = 0;
  private player?: HTMLAudioElement;
  private retry = 0;
  private closedByUser = false;
  private recognizer?: any;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private mesh?: PeerMesh;
  private voice = new VoicePlayer(() => this.unblockAudioOnClick());

  join(meetingId: string, name: string) {
    this.leave();
    this.retry = 0;
    this.tensor.set({ status: 'idle' });
    this.participants.set([]);
    this.transcript.set([]);
    this.interim.set({});
    this.chat.set([]);
    this.documents.set([]);
    this.cards.set([]);
    this.error.set('');
    this.micError.set('');
    this.meetingId.set(meetingId);
    this.me.set(name);
    this.peerStates.set({});
    this.presence.set({});
    this.ended.set(null);
    this.mesh = new PeerMesh(
      name,
      (to, data) => this.send({ type: 'rtc', to, data }),
      (peer, state) =>
        this.peerStates.update((m) => {
          const next = { ...m };
          if (state) next[peer] = state;
          else delete next[peer];
          return next;
        }),
      () => this.unblockAudioOnClick(),
    );
    this.closedByUser = false;
    this.connect();
  }

  leave() {
    this.closedByUser = true;
    clearTimeout(this.reconnectTimer);
    this.stopMic();
    this.stopPlayback();
    this.mesh?.closeAll();
    this.mesh = undefined;
    this.caption.set('');
    this.ws?.close();
    this.ws = undefined;
    this.connection.set('offline');
  }

  private connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${location.host}/ws/${encodeURIComponent(this.meetingId())}?name=${encodeURIComponent(this.me())}`;
    this.connection.set(this.retry ? 'reconnecting' : 'connecting');
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.retry = 0;
      this.connection.set('live');
      this.mesh?.closeAll(); // after a reconnect, voice links are rebuilt from the fresh participant list
      if (this.micOn()) this.send({ type: 'mic', on: true });
    };
    ws.onmessage = (event) => {
      if (this.ws === ws) this.onEvent(JSON.parse(event.data));
    };
    ws.onclose = (event) => {
      if (this.closedByUser || this.ws !== ws) return;
      if (event.code === 4000) {
        if (!this.ended()) this.ended.set({ by: 'an admin' });
        this.leave(); // ended by an admin: don't reconnect
        return;
      }
      if (event.code === 4401) {
        this.leave();
        this.auth.expired(); // session ended: sign in again, then come back to this meeting
        return;
      }
      this.connection.set('reconnecting');
      this.retry++;
      this.reconnectTimer = setTimeout(() => this.connect(), Math.min(1000 * this.retry, 8000));
    };
  }

  private onEvent(message: { event: string; data: any }) {
    const { event, data } = message;
    switch (event) {
      case 'snapshot': {
        const s = data as Snapshot;
        this.tensor.set(s.meeting.tensor ?? { status: 'idle' });
        this.participants.set(s.participants);
        this.transcript.set(s.transcript);
        this.chat.set(s.chat);
        this.documents.set(s.documents);
        this.cards.set(s.cards);
        this.syncPeers();
        break;
      }
      case 'tensor':
        if (data.status === 'listening' && this.tensor().status !== 'listening' && this.voiceOn()) this.voice.chime();
        this.tensor.set(data);
        break;
      case 'participants':
        this.participants.set(data.items);
        this.syncPeers();
        break;
      case 'rtc':
        void this.mesh?.onSignal(data.from, data.data).catch(() => undefined);
        break;
      case 'presence':
        this.presence.update((m) => ({ ...m, [data.name]: { ...m[data.name], ...data } }));
        break;
      case 'interim':
        this.interim.update((m) => ({ ...m, [data.speaker]: data.text }));
        break;
      case 'transcript':
        this.interim.update((m) => {
          const next = { ...m };
          delete next[data.speaker];
          return next;
        });
        this.transcript.update((list) => [...list, data].slice(-300));
        break;
      case 'chat':
        this.chat.update((list) => [...list.filter((m) => m.id !== data.id), data]);
        break;
      case 'document':
        this.documents.update((list) => [...list.filter((d) => d.id !== data.id), data]);
        break;
      case 'card':
        this.cards.update((list) => {
          const i = list.findIndex((c) => c.card_id === data.card_id);
          return i < 0 ? [...list, data] : list.map((c, j) => (j === i ? data : c));
        });
        break;
      case 'speak':
        this.play(data.text, data.audio_mp3_base64);
        break;
      case 'audio':
        if (this.voiceOn() && data.pcm) this.voice.chunk(data.id, data.pcm);
        break;
      case 'ended':
        this.ended.set({ by: data.by });
        break;
      case 'stop_audio':
        this.stopPlayback();
        break;
    }
  }

  // ---- other participants' voices -----------------------------------------------------

  private syncPeers() {
    this.mesh?.sync(this.participants().filter((p) => p.connected).map((p) => p.name));
  }

  /** Browsers may block audio until the user interacts with the page. */
  private unblockAudioOnClick() {
    if (this.audioBlocked()) return;
    this.audioBlocked.set(true);
    document.addEventListener(
      'click',
      () => {
        this.mesh?.resume();
        this.voice.resume();
        this.audioBlocked.set(false);
      },
      { once: true, capture: true },
    );
  }

  isSpeaking(name: string) {
    return name === this.me() ? this.localSpeaking() : !!this.presence()[name]?.speaking;
  }

  // ---- Tensor's voice -------------------------------------------------------------

  private play(text: string, audioBase64: string | null) {
    this.caption.set(text);
    this.stopPlayback();
    if (!audioBase64 || !this.voiceOn()) return;
    const player = new Audio(`data:audio/mpeg;base64,${audioBase64}`);
    this.player = player;
    player
      .play()
      .catch(() =>
        this.error.set('Click anywhere to allow Tensor to speak (browser autoplay rules).'),
      );
  }

  stopPlayback() {
    this.voice.stop();
    if (this.player) {
      this.player.pause();
      this.player = undefined;
    }
  }

  // ---- microphone -------------------------------------------------------------------

  async toggleMic() {
    if (this.micOn()) this.stopMic();
    else await this.startMic();
  }

  async startMic() {
    this.micError.set('');
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      this.audioContext = new AudioContext();
      await this.audioContext.audioWorklet.addModule('/pcm-worklet.js');
      const source = this.audioContext.createMediaStreamSource(this.stream);
      this.worklet = new AudioWorkletNode(this.audioContext, 'pcm-worklet');
      this.worklet.port.onmessage = ({ data }) =>
        this.onFrame(data.pcm as ArrayBuffer, data.rms as number);
      source.connect(this.worklet);
      this.micOn.set(true);
      this.mesh?.setTrack(this.stream.getAudioTracks()[0] ?? null);
      this.send({ type: 'mic', on: true });
      this.startWakeHint();
    } catch (e) {
      this.micError.set(e instanceof Error ? e.message : 'Microphone unavailable');
      this.stopMic();
    }
  }

  stopMic() {
    this.stopWakeHint();
    this.mesh?.setTrack(null);
    if (this.micOn()) this.send({ type: 'mic', on: false });
    this.worklet?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.audioContext?.close().catch(() => undefined);
    this.worklet = undefined;
    this.stream = undefined;
    this.audioContext = undefined;
    this.micOn.set(false);
    this.setSpeaking(false);
  }

  /**
   * Browser speech recognition used only as a fast "Hey Tensor" hint for instant UI feedback.
   * Commands are always taken from the server's Speech-to-Text transcript.
   */
  private startWakeHint() {
    const Ctor = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';
    rec.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text: string = event.results[i][0].transcript.toLowerCase();
        if (hasWakeWord(text)) {
          this.send({ type: 'wake_hint' });
          break;
        }
      }
    };
    rec.onend = () => {
      if (this.micOn() && this.recognizer === rec) rec.start(); // keep listening across pauses
    };
    rec.onerror = () => undefined;
    try {
      rec.start();
      this.recognizer = rec;
      this.wakeHintAvailable.set(true);
    } catch {
      this.wakeHintAvailable.set(false);
    }
  }

  private stopWakeHint() {
    const rec = this.recognizer;
    this.recognizer = undefined;
    try {
      rec?.stop();
    } catch {
      /* already stopped */
    }
  }

  private onFrame(pcm: ArrayBuffer, rms: number) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(pcm);
    const now = performance.now();
    if (rms > VAD_THRESHOLD) {
      this.lastVoice = now;
      if (!this.localSpeaking()) this.setSpeaking(true);
    } else if (this.localSpeaking() && now - this.lastVoice > VAD_HANGOVER_MS) {
      this.setSpeaking(false);
    }
  }

  private setSpeaking(speaking: boolean) {
    this.localSpeaking.set(speaking);
    if (speaking) this.stopPlayback(); // barge-in on this device; the server stops everyone else
    this.send({ type: 'vad', speaking });
  }

  // ---- chat and REST --------------------------------------------------------------------

  send(message: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  sendChat(text: string) {
    this.send({ type: 'chat', text });
  }

  private async request<T>(path: string, method: string, body?: unknown): Promise<T> {
    const response = await fetch(`/api/meetings/${encodeURIComponent(this.meetingId())}${path}`, {
      method,
      headers: body === undefined || body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      this.leave();
      this.auth.expired();
    }
    if (!response.ok) {
      const message = data.error ?? data.detail ?? `Request failed (${response.status})`;
      this.error.set(typeof message === 'string' ? message : JSON.stringify(message));
      throw new Error(message);
    }
    return data as T;
  }

  upload(file: File) {
    const form = new FormData();
    form.append('by', this.me());
    form.append('file', file);
    return this.request('/documents', 'POST', form);
  }

  approve(card: ActionCard) {
    return this.request(`/cards/${card.card_id}/approve`, 'POST', { by: this.me() });
  }

  cancel(card: ActionCard) {
    return this.request(`/cards/${card.card_id}/cancel`, 'POST', { by: this.me() });
  }

  editTask(card: ActionCard, tempId: number, fields: Record<string, unknown>) {
    return this.request(`/cards/${card.card_id}/tasks/${tempId}`, 'PATCH', {
      by: this.me(),
      ...fields,
    });
  }

  editEmail(card: ActionCard, fields: Record<string, unknown>) {
    return this.request(`/cards/${card.card_id}/email`, 'PATCH', { by: this.me(), ...fields });
  }

  /** Download links for a document Tensor wrote. */
  exportUrl(cardId: string, format: 'docx' | 'md') {
    return `/api/meetings/${encodeURIComponent(this.meetingId())}/exports/${encodeURIComponent(cardId)}.${format}`;
  }

  requestMinutes() {
    return this.request('/minutes', 'POST', {});
  }

  latestMinutes() {
    return this.request<{ card: ActionCard | null; writing: boolean }>('/minutes/latest', 'GET');
  }

  endMeeting() {
    return this.request('/end', 'POST', {});
  }

  handSpeak() {
    return this.request('/hand/speak', 'POST', { by: this.me() });
  }

  handDismiss() {
    return this.request('/hand/dismiss', 'POST', { by: this.me() });
  }
}
