import { Component, Output, EventEmitter, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="sidebar">
      <h2>🤖 AI Executive Assistant</h2>
      <hr>
      <div class="organiser-badge">
        <strong>Organiser:</strong> Poojitha Reddy
      </div>

      <div class="prefs-section">
        <h3>⚙️ Default Preferences</h3>
        <p class="subtitle">Set once — applied silently to every new meeting.</p>
        
        <label>
          <strong>Default Duration</strong>
          <select [(ngModel)]="prefs.duration">
            <option *ngFor="let d of durationOptions" [value]="d">{{d}}</option>
          </select>
        </label>

        <label>
          <strong>Default Recurrence</strong>
          <select [(ngModel)]="prefs.recurrence">
            <option *ngFor="let r of recurrenceOptions" [value]="r">{{r}}</option>
          </select>
        </label>

        <label>
          <strong>Default Presenter</strong>
          <input type="text" [(ngModel)]="prefs.presenter" placeholder="Leave blank → Poojitha Reddy">
        </label>

        <button class="save-btn" (click)="savePrefs()">💾 Save Preferences</button>
        <div *ngIf="saveSuccess" class="success-msg">Preferences saved!</div>
      </div>
      <hr>

      <div class="info-section">
        <strong>How to use:</strong><br>
        Type ONE sentence describing your meeting:<br><br>
        🗣️ <em>"Book a 1hr sprint review with Alice and Engineering team next Monday, weekly"</em><br><br>
        AI extracts everything. You just tap to confirm.
      </div>
      <hr>

      <div class="meetings-section">
        <h3>📋 Booked Meetings (Redis)</h3>
        <div *ngIf="meetings.length === 0" class="empty-msg">No meetings booked yet.</div>
        <div class="meeting-card" *ngFor="let m of meetings">
          <strong>{{m.subject || '?'}}</strong>
          <div class="meeting-meta">{{m.start || '?'}} · {{m.recurrence || 'one-time'}}</div>
        </div>
      </div>

      <button class="clear-btn" (click)="clearChat.emit()">🔥 Clear Chat</button>
    </div>
  `,
  styles: [`
    .sidebar { width: 320px; background: #0f172a; padding: 20px; color: #e2e8f0; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; border-right: 1px solid #1e293b; }
    h2 { font-size: 1.2rem; font-weight: 600; margin: 0; }
    hr { border: 0; border-top: 1px solid #1e293b; width: 100%; margin: 0; }
    .organiser-badge { background: #064e3b; color: #34d399; padding: 10px; border-radius: 8px; font-size: 0.9rem; }
    .subtitle { color: #64748b; font-size: 0.8rem; margin: 0 0 15px 0; }
    label { display: flex; flex-direction: column; font-size: 0.9rem; gap: 5px; margin-bottom: 15px; }
    select, input { padding: 8px; border-radius: 6px; background: #1e293b; border: 1px solid #334155; color: #e2e8f0; }
    .save-btn { background: #334155; border: 1px solid #475569; color: white; padding: 10px; border-radius: 6px; cursor: pointer; transition: 0.2s; width: 100%; font-weight: 600; }
    .save-btn:hover { background: #475569; }
    .success-msg { color: #34d399; font-size: 0.85rem; margin-top: 5px; text-align: center; }
    .info-section { background: rgba(59, 130, 246, 0.1); border-left: 3px solid #3b82f6; padding: 15px; font-size: 0.85rem; border-radius: 0 8px 8px 0; }
    .meeting-card { background: #1e293b; padding: 10px; border-radius: 8px; margin-bottom: 10px; }
    .meeting-meta { font-size: 0.8rem; color: #94a3b8; margin-top: 5px; }
    .clear-btn { background: #7f1d1d; color: white; border: none; padding: 12px; border-radius: 6px; cursor: pointer; font-weight: 600; margin-top: auto; }
    .clear-btn:hover { background: #991b1b; }
    .empty-msg { color: #64748b; font-size: 0.85rem; }
  `]
})
export class SidebarComponent {
  @Output() clearChat = new EventEmitter<void>();
  api = inject(ApiService);

  durationOptions = ['30 minutes', '1 hour', '1.5 hours', '2 hours'];
  recurrenceOptions = ['One-time', 'Daily', 'Weekly', 'Bi-weekly', 'Monthly'];
  
  prefs = { duration: '1 hour', recurrence: 'One-time', presenter: '' };
  meetings: any[] = [];
  saveSuccess = false;

  ngOnInit() {
    this.api.getPrefs().subscribe(res => {
      if (res && Object.keys(res).length > 0) {
        this.prefs = { ...this.prefs, ...res };
      }
    });
    this.refreshMeetings();
  }

  refreshMeetings() {
    this.api.getMeetings().subscribe(res => {
      this.meetings = res.meetings || [];
    });
  }

  savePrefs() {
    this.api.savePrefs(this.prefs).subscribe(() => {
      this.saveSuccess = true;
      setTimeout(() => this.saveSuccess = false, 3000);
    });
  }
}
