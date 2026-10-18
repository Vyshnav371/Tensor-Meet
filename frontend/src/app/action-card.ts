import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import type { ActionCard, JiraTask } from './models';
import { Room } from './room.service';
import { Auth } from './auth.service';
import { Icon } from './icon';
import { MarkdownPipe } from './markdown.pipe';


/** Human-in-the-loop review card: nothing executes until a person approves (click or "go ahead"). */
@Component({
  selector: 'tn-action-card',
  imports: [FormsModule, Icon, MarkdownPipe],
  template: `
    @let c = card();
    <article class="card" [attr.data-status]="c.status" [class.done]="!reviewable()">
      <header>
        <span class="card-kind"
          ><tn-icon [name]="c.type === 'EMAIL_DRAFT' ? 'mail' : c.type === 'DOCUMENT' ? 'file' : 'jira'" />{{
            c.type === 'EMAIL_DRAFT'
              ? 'Email draft'
              : c.type === 'DOCUMENT'
                ? c.payload.kind === 'minutes'
                  ? 'Meeting minutes'
                  : 'Document'
                : 'Jira task plan'
          }}</span
        >
        <span class="status-pill" [attr.data-status]="c.status">{{ statusLabel() }}</span>
        @if (c.type !== 'DOCUMENT') {
          <span class="executor" [class.sandbox]="c.executor.startsWith('SANDBOX')">{{
            executorLabel()
          }}</span>
        }
      </header>

      @if (c.type === 'DOCUMENT') {
        <h3>{{ c.payload.title }}</h3>
        <div class="doc-preview md" [innerHTML]="c.payload.markdown ?? '' | markdown"></div>
      } @else if (c.type === 'JIRA_TASK_PLAN') {
        <h3>
          {{ c.payload.epic_title }} <small>· {{ c.payload.project_key }}</small>
        </h3>
        <div class="table-scroll">
          <table class="tasks">
            <thead>
              <tr>
                <th></th>
                <th>#</th>
                <th>Task</th>
                <th>Assignee</th>
                <th>Priority</th>
                <th>Pts</th>
              </tr>
            </thead>
            <tbody>
              @for (t of c.payload.proposed_tasks; track t.temp_id) {
                <tr [class.excluded]="!t.include">
                  <td>
                    <input
                      type="checkbox"
                      [checked]="t.include"
                      [disabled]="!reviewable() || busy()"
                      (change)="editTask(t, { include: !t.include })"
                      [attr.aria-label]="'Include task ' + t.temp_id"
                    />
                  </td>
                  <td>{{ t.temp_id }}</td>
                  <td>
                    <strong>{{ t.title }}</strong>
                    @if (t.acceptance_criteria.length) {
                      <details>
                        <summary>Acceptance criteria</summary>
                        <ul>
                          @for (a of t.acceptance_criteria; track $index) {
                            <li>{{ a }}</li>
                          }
                        </ul>
                      </details>
                    }
                  </td>
                  <td>
                    <select
                      [ngModel]="t.assignee"
                      [attr.aria-label]="'Assignee for task ' + t.temp_id"
                      [disabled]="!reviewable() || busy()"
                      (ngModelChange)="editTask(t, { assignee: $event })"
                    >
                      @for (p of people(t); track p) {
                        <option [value]="p">{{ p }}</option>
                      }
                    </select>
                  </td>
                  <td>
                    <select
                      [ngModel]="t.priority"
                      [attr.aria-label]="'Priority for task ' + t.temp_id"
                      [disabled]="!reviewable() || busy()"
                      (ngModelChange)="editTask(t, { priority: $event })"
                    >
                      @for (p of priorities; track p) {
                        <option [value]="p">{{ p }}</option>
                      }
                    </select>
                  </td>
                  <td>
                    <select
                      [ngModel]="t.story_points"
                      [attr.aria-label]="'Story points for task ' + t.temp_id"
                      [disabled]="!reviewable() || busy()"
                      (ngModelChange)="editTask(t, { story_points: +$event })"
                    >
                      @for (p of points; track p) {
                        <option [ngValue]="p">{{ p }}</option>
                      }
                    </select>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <dl class="email">
          <dt>To</dt>
          <dd>
            @for (p of c.payload.to; track p.email) {
              <span class="chip" [title]="p.email">{{ p.name }}</span>
            }
          </dd>
          <dt>CC</dt>
          <dd>
            @for (p of c.payload.cc; track p.email) {
              <span class="chip" [title]="p.email">{{ p.name }}</span>
            } @empty {
              <span class="muted">none</span>
            }
          </dd>
          <dt>Subject</dt>
          <dd>
            <input
              aria-label="Email subject"
              [ngModel]="c.payload.subject"
              [disabled]="!reviewable() || busy()"
              #subject
              (blur)="saveEmail({ subject: subject.value })"
            />
          </dd>
        </dl>
        <textarea
          aria-label="Email body"
          rows="7"
          [ngModel]="c.payload.body"
          [disabled]="!reviewable() || busy()"
          #body
          (blur)="saveEmail({ body: body.value })"
        ></textarea>
        @if (c.payload.unresolved?.length) {
          <p class="warn">Not in directory (not added): {{ c.payload.unresolved?.join(', ') }}</p>
        }
      }

      @if (c.grounding_sources.length) {
        <p class="sources">Grounded in: {{ c.grounding_sources.join(' · ') }}</p>
      }

      @if (reviewable()) {
        <footer>
          <span class="hint">Edit here or by voice. Say <b>“go ahead”</b> to approve.</span>
          <button class="ghost" [disabled]="busy()" (click)="act('cancel')">Cancel</button>
          <button
            class="primary"
            [disabled]="busy() || (c.type === 'JIRA_TASK_PLAN' && !hasIncludedTasks())"
            (click)="act('approve')"
          >
            <tn-icon name="check" />{{
              busy()
                ? 'Saving…'
                : c.type === 'EMAIL_DRAFT'
                  ? 'Approve & send'
                  : 'Approve & create ' + includedCount()
            }}
          </button>
        </footer>
      } @else {
        <footer class="result">
          @if (c.type === 'DOCUMENT') {
            <span>Written for {{ c.triggered_by }}</span>
            <a class="button primary" [href]="room.exportUrl(c.card_id, 'docx')" download
              ><tn-icon name="file" /> Word (.docx)</a
            >
            <a class="button secondary" [href]="room.exportUrl(c.card_id, 'md')" download>Markdown (.md)</a>
          } @else if (c.status === 'EXECUTED') {
            <span>✓ Approved by {{ c.approved_by }} ({{ channel() }})</span>
            @for (ref of c.result?.refs ?? []; track ref; let i = $index) {
              <a class="chip key" [href]="link(i)" target="_blank" rel="noopener">{{ ref }}</a>
            }
            @if (c.result?.note) {
              <small class="muted">{{ c.result?.note }}</small>
            }
          } @else if (c.status === 'FAILED') {
            <span class="warn">Failed: {{ c.error }}</span>
          } @else {
            <span class="muted">{{ statusLabel() }}</span>
          }
        </footer>
      }
    </article>
  `,
})
export class ActionCardView {
  readonly card = input.required<ActionCard>();
  readonly room = inject(Room);
  private auth = inject(Auth);
  readonly busy = signal(false);
  readonly priorities = ['Highest', 'High', 'Medium', 'Low', 'Lowest'];
  readonly points = [1, 2, 3, 5, 8, 13];

  readonly reviewable = computed(() => ['PENDING_REVIEW', 'MODIFIED'].includes(this.card().status));
  readonly hasIncludedTasks = computed(() =>
    (this.card().payload.proposed_tasks ?? []).some((t) => t.include),
  );
  readonly includedCount = computed(() => {
    const n = (this.card().payload.proposed_tasks ?? []).filter((t) => t.include).length;
    return `${n} ticket${n === 1 ? '' : 's'}`;
  });
  readonly statusLabel = computed(
    () =>
      ({
        PENDING_REVIEW: 'Pending review',
        MODIFIED: 'Modified · pending review',
        APPROVED: 'Approved',
        EXECUTING: 'Executing…',
        EXECUTED: 'Done',
        FAILED: 'Failed',
        CANCELLED: 'Cancelled',
        READY: 'Ready to download',
      })[this.card().status],
  );
  readonly executorLabel = computed(
    () =>
      ({
        SANDBOX_JIRA: 'Sandbox Jira',
        SANDBOX_OUTBOX: 'Sandbox outbox',
        JIRA: 'Jira Cloud',
        GMAIL: 'Gmail',
      })[this.card().executor] ?? this.card().executor,
  );
  readonly channel = computed(() =>
    (this.card().approval_channel ?? '').split(':')[0].replace('_', ' '),
  );

  people(t: JiraTask) {
    const people = [...this.auth.directory().map((p) => p.name), 'Unassigned'];
    return people.includes(t.assignee) ? people : [t.assignee, ...people];
  }

  link(i: number) {
    const href = this.card().result?.links?.[i] ?? '';
    return href.startsWith('http') ? href : null;
  }

  async editTask(t: JiraTask, fields: Record<string, unknown>) {
    await this.run(() => this.room.editTask(this.card(), t.temp_id, fields));
  }

  async saveEmail(fields: { subject?: string; body?: string }) {
    const p = this.card().payload;
    if ((fields.subject ?? p.subject) === p.subject && (fields.body ?? p.body) === p.body) return;
    await this.run(() => this.room.editEmail(this.card(), fields));
  }

  async act(kind: 'approve' | 'cancel') {
    await this.run(() =>
      kind === 'approve' ? this.room.approve(this.card()) : this.room.cancel(this.card()),
    );
  }

  private async run(fn: () => Promise<unknown>) {
    this.busy.set(true);
    try {
      await fn();
    } catch {
      /* error surfaced by the room service */
    } finally {
      this.busy.set(false);
    }
  }
}
