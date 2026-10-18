import {
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ActionCardView } from './action-card';
import { MarkdownPipe } from './markdown.pipe';
import { Room } from './room.service';
import { Auth } from './auth.service';
import type { ActionCard, Example } from './models';
import { Icon } from './icon';
import { DialogShell } from './dialog';

const STATUS_LABEL: Record<string, string> = {
  idle: 'Ready when you are',
  listening: 'I’m listening…',
  thinking: 'Connecting the dots…',
  speaking: 'Sharing a thought',
  hand_raised: 'Something to share',
};
const DEFAULT_EXAMPLES: Example[] = [
  {
    icon: 'file',
    label: 'Check a document',
    text: 'Hey Tensor, does this spec conflict with our internal cloud security guidelines?',
  },
  {
    icon: 'jira',
    label: 'Plan the next steps',
    text: 'Hey Tensor, turn those conflicts into Jira tickets for the security sprint and assign them to David.',
  },
  {
    icon: 'mail',
    label: 'Draft a follow-up',
    text: 'Hey Tensor, email David the links to those new tickets and a short summary.',
  },
  {
    icon: 'sparkle',
    label: 'Find an answer',
    text: 'Hey Tensor, what were our daily active users last week?',
  },
];

type RoomView = 'overview' | 'transcript' | 'actions' | 'files';

@Component({
  selector: 'tn-room',
  imports: [FormsModule, ActionCardView, MarkdownPipe, Icon, DialogShell, RouterLink],
  templateUrl: './meeting-room.html',
})
export class MeetingRoom implements OnInit, OnDestroy {
  readonly room = inject(Room);
  readonly auth = inject(Auth);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  readonly view = signal<RoomView>('overview');
  readonly chatInput = signal('');
  readonly dragging = signal(false);
  readonly uploading = signal(false);
  readonly expanded = signal<string | null>(null);
  readonly showTips = signal(false);
  readonly copied = signal(false);
  readonly elapsed = signal(0);
  readonly theme = signal<'dark' | 'light'>(
    localStorage.getItem('tensor-theme') === 'dark' ? 'dark' : 'light',
  );
  private transcriptEl = viewChild<ElementRef<HTMLElement>>('transcriptList');
  private chatEl = viewChild<ElementRef<HTMLElement>>('chatList');
  private composerEl = viewChild<ElementRef<HTMLInputElement>>('chatComposer');
  private timer?: ReturnType<typeof setInterval>;
  private copyTimer?: ReturnType<typeof setTimeout>;
  private seenCards = new Set<string>();

  readonly nav = [
    { id: 'overview' as const, label: 'Meeting room', icon: 'grid' },
    { id: 'transcript' as const, label: 'Live transcript', icon: 'transcript' },
    { id: 'actions' as const, label: 'Action center', icon: 'checkSquare' },
    { id: 'files' as const, label: 'Shared files', icon: 'folder' },
  ];
  /** Example prompts for this company's template (the demo company keeps the original script). */
  get examples(): Example[] {
    const examples = this.auth.company()?.examples;
    return examples?.length ? examples : DEFAULT_EXAMPLES;
  }
  readonly meetingTitle = computed(() =>
    this.room
      .meetingId()
      .split('-')
      .map((word, i) =>
        /^q\d$/i.test(word)
          ? word.toUpperCase()
          : i === 0
            ? word.charAt(0).toUpperCase() + word.slice(1)
            : word,
      )
      .join(' '),
  );
  readonly statusLabel = computed(() => {
    const state = this.room.tensor();
    return state.status === 'listening' && state.awaiting_command
      ? 'I’m here. Go ahead.'
      : (STATUS_LABEL[state.status] ?? 'Ready when you are');
  });
  readonly hand = computed(() =>
    this.room.tensor().hand?.raised ? this.room.tensor().hand : null,
  );
  readonly interimEntries = computed(() => Object.entries(this.room.interim()));
  readonly docsById = computed(() => new Map(this.room.documents().map((d) => [d.id, d])));
  readonly people = computed(() => this.room.participants().filter((p) => p.connected));
  readonly visibleTranscript = computed(() =>
    this.view() === 'overview' ? this.room.transcript().slice(-4) : this.room.transcript(),
  );
  readonly duration = computed(() => {
    const seconds = this.elapsed();
    return `${Math.floor(seconds / 60)
      .toString()
      .padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
  });
  readonly selectedMessage = computed(() => this.room.chat().find((m) => m.id === this.expanded()));

  constructor() {
    effect(() => {
      const cards = this.room.cards();
      if (
        cards.some(
          (c) =>
            !this.seenCards.has(c.card_id) && ['PENDING_REVIEW', 'MODIFIED'].includes(c.status),
        )
      ) {
        this.view.set('actions');
      }
      this.seenCards = new Set(cards.map((c) => c.card_id));
    });
    effect(() => {
      document.documentElement.dataset['theme'] = this.theme();
    });
    effect(() => {
      this.visibleTranscript();
      this.room.interim();
      queueMicrotask(() => this.scroll(this.transcriptEl()));
    });
    effect(() => {
      this.room.chat();
      queueMicrotask(() => this.scroll(this.chatEl()));
    });
  }

  ngOnInit() {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    const name = this.auth.user()?.name ?? '';
    if (!name.trim()) {
      void this.router.navigate(['/login'], { queryParams: { next: `/m/${id}` } });
      return;
    }
    this.room.join(id, name.trim());
    const started = Date.now();
    this.timer = setInterval(
      () => this.elapsed.set(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
  }

  private endedWatch = effect(() => {
    if (this.room.ended() && !this.finalMinutes()) queueMicrotask(() => this.watchFinalMinutes());
  });

  ngOnDestroy() {
    clearInterval(this.minutesPoll);
    this.room.leave();
    clearInterval(this.timer);
    clearTimeout(this.copyTimer);
  }

  private scroll(ref?: ElementRef<HTMLElement>) {
    const el = ref?.nativeElement;
    if (el) el.scrollTop = el.scrollHeight;
  }

  toggleTheme() {
    const next = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    try {
      localStorage.setItem('tensor-theme', next);
    } catch {
      /* storage unavailable */
    }
  }

  toggleVoice() {
    this.room.voiceOn.set(!this.room.voiceOn());
    if (!this.room.voiceOn()) this.room.stopPlayback();
  }

  async copyLink() {
    try {
      await navigator.clipboard.writeText(
        `${location.origin}/m/${encodeURIComponent(this.room.meetingId())}`,
      );
      this.copied.set(true);
      clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => this.copied.set(false), 2500);
    } catch {
      this.room.error.set(
        `Couldn’t copy the link. Share this meeting ID: ${this.room.meetingId()}`,
      );
    }
  }

  send() {
    const text = this.chatInput().trim();
    if (!text || this.room.connection() !== 'live') return;
    this.room.sendChat(text);
    this.chatInput.set('');
  }

  useExample(text: string) {
    this.chatInput.set(text.replace(/^Hey Tensor,\s*/i, '@Tensor '));
    this.showTips.set(false);
    setTimeout(() => this.composerEl()?.nativeElement.focus());
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    this.dragging.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) void this.upload(file);
  }

  onDragLeave(event: DragEvent) {
    if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null))
      this.dragging.set(false);
  }

  onPick(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) void this.upload(file);
    input.value = '';
  }

  async upload(file: File) {
    if (this.uploading()) return;
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      this.room.error.set('Choose a PDF file to share with Tensor.');
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      this.room.error.set('Please choose a PDF smaller than 15 MB.');
      return;
    }
    this.uploading.set(true);
    try {
      await this.room.upload(file);
    } catch {
      /* surfaced by room */
    } finally {
      this.uploading.set(false);
    }
  }

  async handAction(action: 'speak' | 'dismiss') {
    try {
      await (action === 'speak' ? this.room.handSpeak() : this.room.handDismiss());
    } catch {
      /* surfaced by room */
    }
  }

  /** Voice link to a remote participant, as shown on their tile. */
  voiceLabel(name: string) {
    const state = this.room.peerStates()[name];
    if (state === 'connected') return this.room.presence()[name]?.mic ? 'In the conversation' : 'Listening · mic off';
    if (state === 'failed') return 'Can’t connect audio (network)';
    return 'Connecting audio…';
  }

  initials(name: string) {
    return name
      .split(/\s+/)
      .map((p) => p[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
  time(ts: string) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  readonly writingMinutes = signal(false);
  readonly finalMinutes = signal<ActionCard | null>(null);
  readonly minutesPending = signal(false);
  private minutesPoll?: ReturnType<typeof setInterval>;

  async minutes() {
    this.writingMinutes.set(true);
    try {
      await this.room.requestMinutes();
    } catch {
      /* surfaced by room */
    } finally {
      setTimeout(() => this.writingMinutes.set(false), 8000); // the card appears in the Action center when done
    }
  }

  /** After the meeting ends, offer the minutes Tensor writes from the final conversation. */
  private watchFinalMinutes() {
    clearInterval(this.minutesPoll);
    const since = Date.now();
    this.minutesPending.set(true);
    const check = async () => {
      try {
        const { card, writing } = await this.room.latestMinutes();
        if (card && Date.parse(card.created_at) >= since - 120_000) {
          this.finalMinutes.set(card);
          this.minutesPending.set(false);
          clearInterval(this.minutesPoll);
        } else if (!writing && Date.now() - since > 15_000) {
          this.minutesPending.set(false);
          clearInterval(this.minutesPoll);
        }
      } catch {
        clearInterval(this.minutesPoll);
      }
    };
    this.minutesPoll = setInterval(check, 3000);
    void check();
  }

  async endMeeting() {
    if (!confirm('End this meeting for everyone? Tensor stops, open drafts are cancelled and the transcript is deleted.')) return;
    try {
      await this.room.endMeeting();
    } catch {
      /* surfaced by room */
    }
  }

  rejoin() {
    this.room.join(this.room.meetingId(), this.auth.user()?.name ?? this.room.me());
  }

  leave() {
    void this.router.navigate(['/']);
  }
}
