import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import type { Company, CompanyData, KnowledgeDoc, Member, Person, SessionInfo, User } from './models';

/** Signed-in user and their company. The session lives in an HttpOnly cookie the page can't read. */
@Injectable({ providedIn: 'root' })
export class Auth {
  private router = inject(Router);
  readonly user = signal<User | null>(null);
  readonly company = signal<Company | null>(null);
  readonly directory = signal<Person[]>([]);
  readonly loaded = signal(false);
  readonly isAdmin = computed(() => this.user()?.role === 'admin');
  private pending?: Promise<void>;

  /** Load the session once (guards await this before deciding). */
  load(force = false): Promise<void> {
    if (!this.pending || force) {
      this.pending = this.api<SessionInfo>('/api/auth/me')
        .then((s) => this.apply(s))
        .catch(() => this.apply(null))
        .finally(() => this.loaded.set(true));
    }
    return this.pending;
  }

  private apply(s: SessionInfo | null) {
    this.user.set(s?.user ?? null);
    this.company.set(s?.company ?? null);
    if (s) void this.loadDirectory();
    else this.directory.set([]);
  }

  async loadDirectory() {
    try {
      this.directory.set(await this.api<Person[]>('/api/directory'));
    } catch {
      /* not signed in */
    }
  }

  async login(email: string, password: string) {
    this.apply(await this.api<SessionInfo>('/api/auth/login', 'POST', { email, password }));
    this.pending = Promise.resolve();
  }

  async register(body: Record<string, string>) {
    this.apply(await this.api<SessionInfo>('/api/auth/register', 'POST', body));
    this.pending = Promise.resolve();
  }

  async logout() {
    try {
      await this.api('/api/auth/logout', 'POST', {});
    } finally {
      this.apply(null);
      void this.router.navigate(['/login']);
    }
  }

  /** Called when the server says the session is gone (expired cookie, signed out elsewhere). */
  expired() {
    this.apply(null);
    void this.router.navigate(['/login'], { queryParams: { next: location.pathname } });
  }

  invite(code: string) {
    return this.api<{ company_name: string; industry_label?: string }>(`/api/invites/${encodeURIComponent(code)}`);
  }
  industries() {
    return this.api<{ id: string; label: string }[]>('/api/industries');
  }
  members() {
    return this.api<Member[]>('/api/company/members');
  }
  data() {
    return this.api<CompanyData>('/api/company/data');
  }
  knowledge() {
    return this.api<KnowledgeDoc[]>('/api/company/knowledge');
  }
  uploadKnowledge(file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.api<KnowledgeDoc>('/api/company/knowledge', 'POST', form);
  }
  deleteKnowledge(id: string) {
    return this.api(`/api/company/knowledge/${encodeURIComponent(id)}`, 'DELETE');
  }
  voices() {
    return this.api<{ id: string; label: string; engine: string; selected: boolean }[]>('/api/voices');
  }
  async chooseVoice(voice: string) {
    await this.api('/api/company/voice', 'POST', { voice });
    this.company.update((c) => (c ? { ...c, voice } : c));
  }

  async rotateInvite() {
    const { invite_code } = await this.api<{ invite_code: string }>('/api/company/invite', 'POST', {});
    this.company.update((c) => (c ? { ...c, invite_code } : c));
  }
  async retrySetup() {
    const company = await this.api<Company>('/api/company/retry', 'POST', {});
    this.company.set(company);
  }

  async api<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined || body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data.error ?? data.detail ?? `Request failed (${response.status})`;
      throw Object.assign(new Error(typeof message === 'string' ? message : JSON.stringify(message)), {
        status: response.status,
      });
    }
    return data as T;
  }
}

/** Pages that need a company account. */
export const signedIn: CanActivateFn = async (_route, state) => {
  const auth = inject(Auth);
  const router = inject(Router); // inject() only works before the first await
  await auth.load();
  return auth.user() ? true : router.createUrlTree(['/login'], { queryParams: { next: state.url } });
};

/** Sign-in pages: skip them when already signed in. */
export const signedOut: CanActivateFn = async () => {
  const auth = inject(Auth);
  const router = inject(Router);
  await auth.load();
  return auth.user() ? router.createUrlTree(['/']) : true;
};
