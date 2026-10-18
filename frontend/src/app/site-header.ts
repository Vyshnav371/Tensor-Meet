import { Component, inject, input, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { Auth } from './auth.service';
import { Icon } from './icon';

/** Brand, company switcher links and theme toggle for the pages outside a meeting. */
@Component({
  selector: 'tn-site-header',
  imports: [Icon, RouterLink, RouterLinkActive],
  template: `
    <header class="join-header">
      <a class="brand" routerLink="/">
        <span class="brand-mark"><tn-icon name="logo" /></span>tensor<span class="brand-period">.</span>
      </a>
      <div class="join-header-actions">
        @if (auth.user(); as user) {
          <nav class="site-nav" aria-label="Pages">
            <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }"
              ><tn-icon name="headphones" /> Meetings</a
            >
            <a routerLink="/company" routerLinkActive="active"><tn-icon name="building" /> Company</a>
          </nav>
          <span class="company-chip" [title]="auth.company()?.industry_label ?? ''"
            ><span class="workspace-avatar">{{ (auth.company()?.name ?? '?').charAt(0) }}</span
            >{{ auth.company()?.name }}</span
          >
          <button type="button" class="ghost signout" (click)="auth.logout()" [title]="'Signed in as ' + user.email">
            <tn-icon name="logout" /><span>Sign out</span>
          </button>
        } @else if (note()) {
          <span class="join-header-note"><span class="status-dot"></span> {{ note() }}</span>
        }
        <button
          type="button"
          class="theme-toggle"
          role="switch"
          aria-label="Dark mode"
          [attr.aria-checked]="theme() === 'dark'"
          (click)="toggleTheme()"
        >
          <tn-icon [name]="theme() === 'dark' ? 'moon' : 'sun'" />
          <span class="theme-toggle-label">Dark mode</span>
          <span class="theme-switch-track" [class.checked]="theme() === 'dark'"><span></span></span>
        </button>
      </div>
    </header>
  `,
})
export class SiteHeader {
  readonly auth = inject(Auth);
  readonly note = input('');
  readonly theme = signal<'dark' | 'light'>(readTheme());

  constructor() {
    document.documentElement.dataset['theme'] = this.theme();
  }

  toggleTheme() {
    const next = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    document.documentElement.dataset['theme'] = next;
    try {
      localStorage.setItem('tensor-theme', next);
    } catch {
      /* storage unavailable */
    }
  }
}

function readTheme(): 'dark' | 'light' {
  try {
    return localStorage.getItem('tensor-theme') === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}
