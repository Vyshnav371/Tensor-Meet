import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Auth } from './auth.service';
import { Icon } from './icon';
import { SiteHeader } from './site-header';

@Component({
  selector: 'tn-join',
  imports: [FormsModule, Icon, SiteHeader],
  template: `
    <main class="join-page">
      <tn-site-header />
      <div class="join-layout">
        <section class="join-story">
          <span class="eyebrow"><span class="eyebrow-line"></span> YOUR NEW MEETING TEAMMATE</span>
          <h1>Good conversations.<br /><em>Great follow-through.</em></h1>
          <p class="join-description">
            Stay in the conversation. Tensor brings the answers,<br class="desktop-break" />
            connects the dots, and helps move the work forward.
          </p>
          <div
            class="join-illustration"
            aria-label="Illustration of Tensor turning meeting context into reviewed actions"
          >
            <div class="illustration-grid"></div>
            <div class="orbit orbit-one"></div>
            <div class="orbit orbit-two"></div>
            <div class="illustration-orb">
              <div class="tensor-orb"><span></span><i></i><b></b></div>
            </div>
            <div class="float-card float-context">
              <span class="mini-icon"><tn-icon name="chat" /></span>
              <div>
                <strong>In the conversation</strong><small>Listening. Connecting. Ready.</small>
              </div>
              <span class="mini-wave"><i></i><i></i><i></i><i></i><i></i></span>
            </div>
            <div class="float-card float-action">
              <span class="mini-icon green"><tn-icon name="checkSquare" /></span>
              <div>
                <strong>From discussion to done</strong><small>With your go-ahead. Always.</small>
              </div>
              <span class="tiny-check"><tn-icon name="check" /></span>
            </div>
            <span class="illustration-caption">A little less follow-up. A little more flow.</span>
          </div>
        </section>
        <section class="join-card">
          <div class="join-card-heading">
            <span class="mini-icon green"><tn-icon name="headphones" /></span
            ><span class="eyebrow">LET’S GET STARTED</span>
          </div>
          <h2>Take a seat.</h2>
          <p>Your next productive meeting starts here.</p>
          <form (ngSubmit)="join()">
            <div class="signed-in-as">
              <span class="avatar">{{ initials() }}</span>
              <div>
                <strong>{{ auth.user()?.name }}</strong
                ><small>Joining as a member of {{ auth.company()?.name }}</small>
              </div>
            </div>
            <label for="join-meeting">Meeting ID <span>Share this with your team</span></label>
            <div class="input-with-icon">
              <tn-icon name="link" /><input
                id="join-meeting"
                name="meeting"
                [(ngModel)]="meeting"
                required
                pattern="[a-z0-9-]{3,40}"
                placeholder="your-meeting-id"
                aria-describedby="meeting-hint"
              />
            </div>
            <small id="meeting-hint" class="field-hint"
              >3–40 lowercase letters, numbers, or hyphens.</small
            >
            <button class="primary join-submit" type="submit" [disabled]="!valid()">
              Join meeting <tn-icon name="arrow" />
            </button>
          </form>
          <div class="join-privacy">
            <tn-icon name="shield" />
            <p>
              You’re in control. Your mic starts off. When enabled, transcription keeps a rolling
              15-minute window. Every external action needs your approval.
            </p>
          </div>
          <div class="join-card-footer">
            <span><tn-icon name="sparkle" /> Grounded answers</span
            ><span><tn-icon name="checkSquare" /> Reviewed actions</span>
          </div>
        </section>
      </div>
      <footer class="join-footer">
        <span>Less busywork. More being here.</span
        ><span><span class="status-dot"></span> {{ auth.company()?.name }}</span>
      </footer>
    </main>
  `,
})
export class JoinPage {
  readonly auth = inject(Auth);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  meeting = this.auth.company()?.industry === 'demo' ? 'q4-security-review' : 'team-sync';

  constructor() {
    const sharedMeeting = this.route.snapshot.queryParamMap.get('meeting');
    if (sharedMeeting && /^[a-z0-9-]{3,40}$/.test(sharedMeeting)) this.meeting = sharedMeeting;
  }

  valid() {
    return /^[a-z0-9-]{3,40}$/.test(this.meeting);
  }

  initials() {
    return (this.auth.user()?.name ?? '?')
      .split(/\s+/)
      .map((p) => p[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }

  join() {
    if (!this.valid()) return;
    void this.router.navigate(['/m', this.meeting]);
  }
}
