import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Auth } from './auth.service';
import { Icon } from './icon';
import { SiteHeader } from './site-header';

type Mode = 'login' | 'register' | 'invite';

/** Sign in, register a company, or join one from an invite link. */
@Component({
  selector: 'tn-auth',
  imports: [FormsModule, Icon, RouterLink, SiteHeader],
  template: `
    <main class="join-page">
      <tn-site-header note="Your company’s knowledge, in every meeting." />
      <div class="join-layout">
        <section class="join-story">
          <span class="eyebrow"><span class="eyebrow-line"></span> A TEAMMATE THAT KNOWS YOUR COMPANY</span>
          <h1>Your company’s knowledge.<br /><em>In every meeting.</em></h1>
          <p class="join-description">
            Register your company once. Tensor answers from your documents and your data,<br
              class="desktop-break"
            />
            and drafts the follow-ups for your team to approve.
          </p>
          <ul class="auth-points">
            <li>
              <span class="mini-icon green"><tn-icon name="file" /></span>
              <div><strong>Your documents</strong><small>Policies and handbooks, searched with Vertex AI Search.</small></div>
            </li>
            <li>
              <span class="mini-icon green"><tn-icon name="database" /></span>
              <div><strong>Your data</strong><small>Questions answered from your own BigQuery tables.</small></div>
            </li>
            <li>
              <span class="mini-icon green"><tn-icon name="lock" /></span>
              <div><strong>Only your company</strong><small>Meetings, files and data are never shared across companies.</small></div>
            </li>
          </ul>
        </section>

        <section class="join-card">
          <div class="join-card-heading">
            <span class="mini-icon green"><tn-icon [name]="mode() === 'login' ? 'lock' : 'building'" /></span
            ><span class="eyebrow">{{ eyebrow() }}</span>
          </div>
          <h2>{{ heading() }}</h2>
          <p>{{ subheading() }}</p>

          @if (mode() === 'invite' && inviteError()) {
            <p class="form-error" role="alert">{{ inviteError() }}</p>
          } @else {
            <form (ngSubmit)="submit()">
              @if (mode() !== 'login') {
                <label for="auth-name">Your name</label>
                <div class="input-with-icon">
                  <tn-icon name="users" /><input id="auth-name" name="name" [(ngModel)]="name" required maxlength="40"
                    autocomplete="name" placeholder="e.g. Alex Morgan" />
                </div>
              }
              <label for="auth-email">Work email</label>
              <div class="input-with-icon">
                <tn-icon name="mail" /><input id="auth-email" name="email" type="email" [(ngModel)]="email" required
                  autocomplete="email" placeholder="you@company.com" />
              </div>
              <label for="auth-password">Password @if (mode() !== 'login') {<span>At least 8 characters</span>}</label>
              <div class="input-with-icon">
                <tn-icon name="lock" /><input id="auth-password" name="password" type="password" [(ngModel)]="password"
                  required minlength="8" [attr.autocomplete]="mode() === 'login' ? 'current-password' : 'new-password'" />
              </div>
              @if (mode() === 'register') {
                <label for="auth-company">Company name</label>
                <div class="input-with-icon">
                  <tn-icon name="building" /><input id="auth-company" name="company" [(ngModel)]="companyName" required
                    maxlength="60" autocomplete="organization" placeholder="e.g. Blue Harbor Pay" />
                </div>
                <label for="auth-industry">Industry <span>Starts you with realistic sample data</span></label>
                <div class="input-with-icon">
                  <tn-icon name="chart" /><select id="auth-industry" name="industry" [(ngModel)]="industry" required>
                    @for (i of industries(); track i.id) {
                      <option [value]="i.id">{{ i.label }}</option>
                    }
                  </select>
                </div>
              }
              @if (error()) {
                <p class="form-error" role="alert">{{ error() }}</p>
              }
              <button class="primary join-submit" type="submit" [disabled]="busy() || !canSubmit()">
                {{ busy() ? 'One moment…' : action() }} <tn-icon name="arrow" />
              </button>
            </form>
          }

          <div class="join-privacy">
            <tn-icon name="shield" />
            <p>
              @switch (mode()) {
                @case ('login') {
                  New here? <a routerLink="/register">Register your company</a>. Joining a colleague? Open the invite
                  link they shared.
                }
                @case ('register') {
                  Already have an account? <a routerLink="/login">Sign in</a>. Jira and email actions run in sandbox mode:
                  nothing leaves Tensor.
                }
                @default {
                  Already have an account? <a routerLink="/login">Sign in</a>. Each account belongs to one company.
                }
              }
            </p>
          </div>
          <div class="join-card-footer">
            <span><tn-icon name="sparkle" /> Grounded answers</span
            ><span><tn-icon name="checkSquare" /> Reviewed actions</span>
          </div>
        </section>
      </div>
      <footer class="join-footer">
        <span>Less busywork. More being here.</span><span><span class="status-dot"></span> Private to your company</span>
      </footer>
    </main>
  `,
})
export class AuthPage implements OnInit {
  private auth = inject(Auth);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  readonly mode = signal<Mode>('login');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly inviteCompany = signal('');
  readonly inviteError = signal('');
  readonly industries = signal<{ id: string; label: string }[]>([]);
  name = '';
  email = '';
  password = '';
  companyName = '';
  industry = 'fintech';
  private code = '';

  readonly eyebrow = computed(() =>
    ({ login: 'WELCOME BACK', register: 'REGISTER YOUR COMPANY', invite: 'YOU’RE INVITED' })[this.mode()],
  );
  readonly heading = computed(() =>
    ({
      login: 'Sign in.',
      register: 'Set up your company.',
      invite: this.inviteCompany() ? `Join ${this.inviteCompany()}.` : 'Join your team.',
    })[this.mode()],
  );
  readonly subheading = computed(() =>
    ({
      login: 'Pick up where your team left off.',
      register: 'You’ll be its admin, and can invite your team next.',
      invite: 'Create your account to join your team’s meetings.',
    })[this.mode()],
  );
  readonly action = computed(() => ({ login: 'Sign in', register: 'Create company', invite: 'Join company' })[this.mode()]);

  async ngOnInit() {
    const path = this.route.snapshot.routeConfig?.path ?? '';
    this.code = this.route.snapshot.paramMap.get('code') ?? '';
    this.mode.set(this.code ? 'invite' : path === 'register' ? 'register' : 'login');
    if (this.mode() === 'register') {
      try {
        this.industries.set(await this.auth.industries());
      } catch {
        this.error.set('Couldn’t load industries. Refresh to try again.');
      }
    }
    if (this.code) {
      try {
        this.inviteCompany.set((await this.auth.invite(this.code)).company_name);
      } catch (e) {
        this.inviteError.set(e instanceof Error ? e.message : 'This invite link is no longer valid.');
      }
    }
  }

  canSubmit() {
    const base = this.email.trim() && this.password.length >= (this.mode() === 'login' ? 1 : 8);
    if (this.mode() === 'login') return !!base;
    if (this.mode() === 'invite') return !!(base && this.name.trim());
    return !!(base && this.name.trim() && this.companyName.trim().length >= 2 && this.industry);
  }

  async submit() {
    if (!this.canSubmit() || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      if (this.mode() === 'login') await this.auth.login(this.email, this.password);
      else
        await this.auth.register({
          name: this.name.trim(),
          email: this.email.trim(),
          password: this.password,
          ...(this.mode() === 'invite'
            ? { invite: this.code }
            : { company_name: this.companyName.trim(), industry: this.industry }),
        });
      const next = this.route.snapshot.queryParamMap.get('next');
      const target = this.mode() === 'register' ? '/company' : next && next.startsWith('/') && !next.startsWith('//') ? next : '/';
      void this.router.navigateByUrl(target);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Something went wrong. Please try again.');
    } finally {
      this.busy.set(false);
    }
  }
}
