import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe, UpperCasePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Auth } from './auth.service';
import { Icon } from './icon';
import type { CompanyData, KnowledgeDoc, Member } from './models';
import { SiteHeader } from './site-header';

const ACCEPT = '.pdf,.html,.htm,.txt,.docx';

/** The company workspace: setup status, invites, members, BigQuery data and searchable documents. */
@Component({
  selector: 'tn-company',
  imports: [DecimalPipe, Icon, RouterLink, SiteHeader, UpperCasePipe],
  template: `
    <main class="join-page company-page">
      <tn-site-header />
      @if (auth.company(); as company) {
        <section class="company-hero">
          <div>
            <span class="eyebrow"><span class="eyebrow-line"></span> {{ company.industry_label | uppercase }}</span>
            <h1>{{ company.name }}</h1>
            <p class="join-description">
              Everyone here shares meetings, documents and data, and nobody outside {{ company.name }} can see them.
            </p>
          </div>
          <a class="button primary" routerLink="/">Start a meeting <tn-icon name="arrow" /></a>
        </section>

        @switch (company.status) {
          @case ('PROVISIONING') {
            <div class="setup-banner" role="status">
              <span class="tensor-orb small" data-status="thinking"><span></span><i></i><b></b></span>
              <div>
                <strong>Setting up {{ company.name }}…</strong>
                <small>Creating your BigQuery dataset, generating {{ company.industry_label }} sample data and indexing your
                  starter handbook. This takes about a minute.</small>
              </div>
            </div>
          }
          @case ('FAILED') {
            <div class="setup-banner failed" role="alert">
              <div>
                <strong>Setup didn’t finish.</strong><small>{{ company.error }}</small>
              </div>
              @if (auth.isAdmin()) {
                <button class="secondary" (click)="retry()"><tn-icon name="refresh" /> Try again</button>
              }
            </div>
          }
        }

        <div class="company-grid">
          <section class="panel">
            <header class="panel-head">
              <span class="mini-icon green"><tn-icon name="users" /></span>
              <div><h2>Team</h2><small>{{ members().length }} {{ members().length === 1 ? 'member' : 'members' }}</small></div>
            </header>
            @if (auth.isAdmin()) {
              <div class="invite-box">
                <label for="invite-link">Invite link <span>Anyone with it can join {{ company.name }}</span></label>
                <div class="invite-row">
                  <input id="invite-link" readonly [value]="inviteUrl()" (focus)="$any($event.target).select()" />
                  <button class="primary" (click)="copyInvite()"><tn-icon [name]="copied() ? 'check' : 'copy'" />
                    {{ copied() ? 'Copied' : 'Copy' }}</button>
                </div>
                <button class="text-button" (click)="rotate()"><tn-icon name="refresh" /> Create a new link (the old one stops working)</button>
              </div>
            } @else {
              <p class="muted small">Ask your admin for an invite link to add people.</p>
            }
            <ul class="member-list">
              @for (m of members(); track m.uid) {
                <li>
                  <span class="avatar">{{ initials(m.name) }}</span>
                  <div><strong>{{ m.name }}</strong><small>{{ m.email }}</small></div>
                  <span class="role-pill" [class.admin]="m.role === 'admin'">{{ m.role }}</span>
                </li>
              }
            </ul>
            <p class="muted small">
              Tensor also knows your template team for drafting tickets and emails. Jira key:
              <b>{{ company.jira_key }}</b>. Jira and email actions run in sandbox mode.
            </p>
          </section>

          <section class="panel">
            <header class="panel-head">
              <span class="mini-icon green"><tn-icon name="file" /></span>
              <div><h2>Knowledge</h2><small>Searched by Tensor with Vertex AI Search</small></div>
              @if (auth.isAdmin()) {
                <label class="button secondary upload-button" [class.disabled]="uploading()">
                  <tn-icon name="upload" /> {{ uploading() ? 'Uploading…' : 'Add document' }}
                  <input type="file" [accept]="accept" (change)="upload($event)" [disabled]="uploading()" hidden />
                </label>
              }
            </header>
            @if (error()) {
              <p class="form-error" role="alert">{{ error() }}</p>
            }
            <ul class="doc-list">
              @for (d of docs(); track d.id) {
                <li>
                  <tn-icon name="file" />
                  <div>
                    <a [href]="'/api/company/knowledge/' + d.id + '/file'" target="_blank" rel="noopener">{{ d.title }}</a>
                    <small>{{ size(d.size) }} · added by {{ d.uploaded_by }}</small>
                  </div>
                  <span class="status-pill" [attr.data-status]="d.status"
                    >{{ d.status === 'READY' ? 'Searchable' : d.status === 'INDEXING' ? 'Indexing…' : 'Failed' }}</span
                  >
                  @if (auth.isAdmin()) {
                    <button class="icon" (click)="remove(d)" [attr.aria-label]="'Remove ' + d.title"><tn-icon name="trash" /></button>
                  }
                </li>
              } @empty {
                <li class="muted">No documents yet. {{ auth.isAdmin() ? 'Add a policy, handbook or spec (PDF, HTML, TXT or DOCX).' : '' }}</li>
              }
            </ul>
            <p class="muted small">New documents become searchable a few minutes after indexing finishes.</p>
          </section>

          <section class="panel wide">
            <header class="panel-head">
              <span class="mini-icon green"><tn-icon name="volume" /></span>
              <div><h2>Tensor’s voice</h2><small>Gemini voices sound most natural; Chirp voices start talking fastest</small></div>
            </header>
            <div class="voice-grid">
              @for (v of voices(); track v.id) {
                <div class="voice-option" [class.selected]="v.selected">
                  <button class="icon" (click)="preview(v.id)" [attr.aria-label]="'Play a sample of ' + v.label">
                    <tn-icon [name]="playing() === v.id ? 'volume' : 'sparkle'" />
                  </button>
                  <div><strong>{{ v.label }}</strong><small>{{ v.engine }}</small></div>
                  @if (v.selected) {
                    <span class="role-pill admin">In use</span>
                  } @else if (auth.isAdmin()) {
                    <button class="text-button" (click)="choose(v.id)">Use</button>
                  }
                </div>
              }
            </div>
          </section>

          <section class="panel wide">
            <header class="panel-head">
              <span class="mini-icon green"><tn-icon name="database" /></span>
              <div><h2>Company data</h2><small>BigQuery dataset <code>{{ data()?.dataset ?? '…' }}</code> · Tensor writes read-only SQL over these tables</small></div>
            </header>
            <div class="table-cards">
              @for (t of data()?.tables ?? []; track t.name) {
                <details class="table-card">
                  <summary>
                    <strong>{{ t.name }}</strong>
                    <small>{{ t.rows | number }} rows @if (t.last_day) { · {{ t.first_day }} → {{ t.last_day }} }</small>
                    <span>{{ t.description }}</span>
                  </summary>
                  <ul>
                    @for (c of t.columns; track c.name) {
                      <li><code>{{ c.name }}</code> <small>{{ c.type }}</small> {{ c.description }}</li>
                    }
                  </ul>
                </details>
              } @empty {
                <p class="muted">{{ company.status === 'PROVISIONING' ? 'Your tables are being created…' : 'No tables found.' }}</p>
              }
            </div>
          </section>
        </div>
      }
    </main>
  `,
})
export class CompanyPage implements OnInit, OnDestroy {
  readonly auth = inject(Auth);
  readonly members = signal<Member[]>([]);
  readonly docs = signal<KnowledgeDoc[]>([]);
  readonly data = signal<CompanyData | null>(null);
  readonly uploading = signal(false);
  readonly copied = signal(false);
  readonly error = signal('');
  readonly voices = signal<{ id: string; label: string; engine: string; selected: boolean }[]>([]);
  readonly playing = signal<string | null>(null);
  private sample?: HTMLAudioElement;
  readonly accept = ACCEPT;
  readonly inviteUrl = computed(() => `${location.origin}/invite/${this.auth.company()?.invite_code ?? ''}`);
  private poll?: ReturnType<typeof setInterval>;

  async ngOnInit() {
    await this.refresh();
    this.poll = setInterval(() => void this.tick(), 4000);
  }

  ngOnDestroy() {
    clearInterval(this.poll);
    this.sample?.pause();
  }

  /** Follow setup and indexing until they settle. */
  private async tick() {
    const provisioning = this.auth.company()?.status === 'PROVISIONING';
    const indexing = this.docs().some((d) => d.status === 'INDEXING');
    if (!provisioning && !indexing) return;
    if (provisioning) {
      await this.auth.load(true);
      if (this.auth.company()?.status !== 'PROVISIONING') await this.refresh();
    }
    if (indexing) this.docs.set(await this.auth.knowledge().catch(() => this.docs()));
  }

  async refresh() {
    const [members, docs, data, voices] = await Promise.allSettled([
      this.auth.members(), this.auth.knowledge(), this.auth.data(), this.auth.voices()]);
    if (voices.status === 'fulfilled') this.voices.set(voices.value);
    if (members.status === 'fulfilled') this.members.set(members.value);
    if (docs.status === 'fulfilled') this.docs.set(docs.value);
    if (data.status === 'fulfilled') this.data.set(data.value);
    void this.auth.loadDirectory();
  }

  async preview(id: string) {
    this.sample?.pause();
    this.sample = new Audio(`/api/voices/${encodeURIComponent(id)}/preview`);
    this.playing.set(id);
    this.sample.onended = () => this.playing.set(null);
    await this.sample.play().catch(() => this.playing.set(null));
  }

  async choose(id: string) {
    try {
      await this.auth.chooseVoice(id);
      this.voices.update((list) => list.map((v) => ({ ...v, selected: v.id === id })));
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Couldn’t change the voice.');
    }
  }

  async upload(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      this.error.set('Please choose a file smaller than 10 MB.');
      return;
    }
    this.uploading.set(true);
    this.error.set('');
    try {
      const doc = await this.auth.uploadKnowledge(file);
      this.docs.update((list) => [doc, ...list]);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Upload failed.');
    } finally {
      this.uploading.set(false);
    }
  }

  async remove(doc: KnowledgeDoc) {
    if (!confirm(`Remove “${doc.title}” from ${this.auth.company()?.name}'s knowledge?`)) return;
    try {
      await this.auth.deleteKnowledge(doc.id);
      this.docs.update((list) => list.filter((d) => d.id !== doc.id));
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Couldn’t remove the document.');
    }
  }

  async copyInvite() {
    try {
      await navigator.clipboard.writeText(this.inviteUrl());
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2500);
    } catch {
      this.error.set('Couldn’t copy. Select the link and copy it manually.');
    }
  }

  async rotate() {
    if (!confirm('Create a new invite link? The current link will stop working.')) return;
    await this.auth.rotateInvite().catch((e) => this.error.set(e.message));
  }

  async retry() {
    await this.auth.retrySetup().catch((e) => this.error.set(e.message));
  }

  size(bytes: number) {
    return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  initials(name: string) {
    return name.split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase();
  }
}
