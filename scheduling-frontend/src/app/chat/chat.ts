import { Component, ChangeDetectorRef, ViewChild, ElementRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api';
import { interval, Subscription, Subject, map, debounceTime, distinctUntilChanged, switchMap, forkJoin, of } from 'rxjs';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  links?: string[];
  options?: string[];
  optionType?: string;
  titledSections?: any;
  existingMeeting?: any;
  meetingData?: any;
  candidateOptions?: string[];
  selectionMap?: Record<string, string>;
  isInteractive?: boolean;
  intent?: string;
}

interface DisambigPerson {
  label: string;   // full raw option string from backend
  name: string;
  dept: string;
  eid: string;
  selected: boolean;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  styleUrl: './chat.css',
  template: `
    <div class="chat-wrapper">
      <div class="chat-container">
        
        <div class="messages" #scrollContainer>
          <div *ngFor="let msg of messages; let i = index" class="message-row">
            <div [class]="msg.role === 'user' ? 'msg user-msg' : 'msg assistant-msg'">
              <ng-container *ngIf="editingIndex !== i; else editTpl">
                <div class="msg-content" [innerHTML]="formatMessage(msg.content)"></div>
                <button *ngIf="msg.role === 'user'" class="edit-action" (click)="startEdit(i, msg.content)" title="Edit message">
                  Edit
                </button>
                <button *ngIf="msg.role === 'assistant' && i > 0" class="edit-action" (click)="regenerate(i)" title="Regenerate response">
                  Resend
                </button>
                <button *ngIf="msg.role === 'assistant' && msg.meetingData" class="edit-action" (click)="openMeetingEditor(msg.meetingData)" title="Edit meeting">
                  Edit Meeting
                </button>
              </ng-container>
              
              <ng-template #editTpl>
                <div class="edit-area">
                  <textarea [(ngModel)]="editPrompt" rows="3"></textarea>
                  <div class="edit-actions">
                    <button class="primary-btn small" (click)="saveEdit(i)">Save & Submit</button>
                    <button class="ghost-btn small" (click)="cancelEdit()">Cancel</button>
                  </div>
                </div>
              </ng-template>
                        
              <!-- Join Links -->
              <div *ngFor="let link of msg.links" class="join-box">
                <b>Join Meeting</b><br>
                <a [href]="link" target="_blank">{{link}}</a>
              </div>

              <!-- Interactive Sections (last assistant message only) -->
              <div *ngIf="msg.isInteractive && i === messages.length - 1" class="interactive-area">
                
                <!-- GATHERING CARD: 1:1 slot_selection and group_selection -->
                <div *ngIf="msg.optionType === 'gathering_card'" class="card-wrapper">
                  <ng-container *ngFor="let section of objectKeys(msg.titledSections || {})">
                    <div class="section-container">
                      <p class="section-header">{{ section }}</p>

                      <!-- MULTI-SELECT presenter section -->
                      <ng-container *ngIf="isMultiSection(section)">
                        <div class="multi-chip-area">
                          <button
                            *ngFor="let opt of filterOpts(msg.titledSections[section])"
                            class="chip-btn"
                            [class.chip-selected]="isChipSelected(section, opt)"
                            (click)="onChipToggle(section, opt, msg)"
                          >
                            {{ cleanOpt(opt) }}
                          </button>
                        </div>
                        <p class="multi-hint">Select one or more participants</p>
                      </ng-container>

                      <!-- SINGLE-SELECT buttons -->
                      <ng-container *ngIf="!isMultiSection(section) && !isTextSection(section)">
                        <div class="stacked-buttons">
                          <button 
                            *ngFor="let opt of filterOpts(msg.titledSections[section])" 
                            class="choice-btn"
                            [class.selected]="opt.startsWith('✅')"
                            (click)="onCardTap(section, opt, msg)"
                          >
                            <span class="btn-label">{{ cleanOpt(opt) }}</span>
                          </button>
                        </div>
                      </ng-container>

                      <!-- TEXT INPUT section -->
                      <ng-container *ngIf="isTextSection(section)">
                        <div class="text-input-area">
                          <input 
                            type="text" 
                            class="card-text-input" 
                            placeholder="Type here..." 
                            [value]="cardSelections[section] || ''"
                            (input)="onTextInput(section, $event)"
                          >
                        </div>
                      </ng-container>

                    </div>
                  </ng-container>

                  <!-- Confirm & Book (disabled until all sections have a selection) -->
                  <div class="confirm-row">
                    <button 
                      class="primary-btn full" 
                      [disabled]="!isGatheringComplete(msg)"
                      (click)="onConfirmCard(msg)"
                    >
                      Confirm & Book
                    </button>
                  </div>
                </div>

                <!-- CONFLICT -->
                <div *ngIf="msg.optionType === 'conflict'" class="card-wrapper">
                  <p class="section-header">Available Alternatives</p>
                  <div class="stacked-buttons">
                    <button 
                      *ngFor="let opt of (msg.options || [])" 
                      class="choice-btn"
                      [class.proceed-btn]="opt.startsWith('Proceed with original')"
                      (click)="onConflictTap(opt)"
                    >
                      {{ opt }}
                    </button>
                  </div>
                </div>

                <!-- EDIT GRID (Post-booking) -->
                <div *ngIf="msg.optionType === 'edit_grid'" class="edit-grid">
                  <button 
                    *ngFor="let opt of msg.options" 
                    class="ghost-btn small"
                    (click)="handleEditTapped(opt, msg.meetingData)"
                  >
                    {{ opt }}
                  </button>
                </div>

                <!-- DISAMBIGUATION CARD -->
                <div *ngIf="msg.intent === 'attendee_disambiguation' || msg.optionType === 'attendee_disambiguation'" class="disambig-card">
                  <div class="disambig-header">
                    <span class="disambig-title">Select Attendee</span>
                    <button class="ghost-btn tiny" (click)="toggleDisambigMode()">
                      {{ disambigBulkMode ? 'Bulk' : 'Single' }}
                    </button>
                  </div>
                  <div class="disambig-grid">
                    <button
                      *ngFor="let p of getDisambigPeople(msg.options || [])"
                      class="person-card"
                      [class.person-selected]="isPersonSelected(p)"
                      (click)="onPersonTap(p, msg)"
                    >
                      <div class="person-avatar">{{ p.name.charAt(0) }}</div>
                      <div class="person-info">
                        <span class="person-name">{{ p.name }}</span>
                        <span class="person-dept">{{ p.dept }}</span>
                      </div>
                    </button>
                  </div>
                  <button
                    class="primary-btn full mt-1"
                    [disabled]="selectedDisambigPeople.size === 0"
                    (click)="confirmDisambig(msg)"
                  >
                    {{ disambigBulkMode ? 'Add Selected (' + selectedDisambigPeople.size + ')' : 'Confirm Selection' }}
                  </button>
                </div>

                <!-- GENERAL fallback -->
                <div *ngIf="msg.intent !== 'attendee_disambiguation' && msg.optionType !== 'attendee_disambiguation' && !['gathering_card', 'edit_grid', 'conflict'].includes(msg.optionType || '') && (msg.options?.length || 0) > 0" class="stacked-buttons mt-1">
                  <button 
                    *ngFor="let opt of msg.options" 
                    class="choice-btn"
                    (click)="sendAction(opt)"
                  >
                    {{ opt }}
                  </button>
                </div>

              </div>

            </div>
          </div>
          
          <!-- Loading Spinner -->
          <div *ngIf="loading" class="thinking-wrapper">
            <div class="spinner-rotate">
              <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="32" stroke-linecap="round" />
              </svg>
            </div>
            <div class="thinking-text">
              {{ loadingMessage }}
            </div>
          </div>
        </div>

        <div class="input-area">
          <input 
            type="text" 
            [(ngModel)]="prompt" 
            (keyup.enter)="sendMessage()" 
            placeholder="Type your request..."
          >
          <button class="send-btn" (click)="sendMessage()" [disabled]="!prompt.trim()">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
          </button>
        </div>

        <div *ngIf="showMeetingEditor" class="editor-overlay">
          <div class="editor-card" [style.transform]="'translate(' + dragX + 'px, ' + dragY + 'px)'">
            <h3 (mousedown)="onDragStart($event)" 
                (document:mousemove)="onDrag($event)" 
                (document:mouseup)="onDragEnd()"
                style="cursor: move;">Edit Meeting</h3>
            
            <div class="form-grid">
              <div class="form-field">
                <label>Title</label>
                <input [(ngModel)]="meetingEdit.subject" type="text">
              </div>
              <div class="form-field">
                <label>Location</label>
                <input [(ngModel)]="meetingEdit.location" type="text" list="location-opts">
                <datalist id="location-opts">
                  <option value="Virtual"></option>
                  <option value="Nilgiri"></option>
                  <option value="Himalaya"></option>
                </datalist>
              </div>
              <div class="form-field">
                <label>Date</label>
                <input [(ngModel)]="meetingEdit.date" type="date">
              </div>
              <div class="form-field">
                <label>Time</label>
                <input [(ngModel)]="meetingEdit.time" type="time">
              </div>
              <div class="form-field">
                <label>Duration</label>
                <select [(ngModel)]="meetingEdit.duration">
                  <option [value]="30">30 min</option>
                  <option [value]="45">45 min</option>
                  <option [value]="60">1 hour</option>
                  <option [value]="90">1.5 hours</option>
                  <option [value]="120">2 hours</option>
                </select>
              </div>
              <div class="form-field">
                <label>Presenter</label>
                <select [(ngModel)]="meetingEdit.presenter">
                   <option value="">Organiser</option>
                   <option *ngFor="let p of meetingEdit.attendeeNames" [value]="p">{{ p }}</option>
                </select>
              </div>
              <div class="form-field">
                <label>Recurrence</label>
                <select [(ngModel)]="meetingEdit.recurrence">
                  <option value="none">One-time</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="biweekly">Bi-weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
              <div class="form-field" *ngIf="meetingEdit.recurrence !== 'none'">
                <label>Series End Date</label>
                <input [(ngModel)]="meetingEdit.recurrence_end_date" type="date">
              </div>
            </div>

            <label>Agenda</label>
            <textarea [(ngModel)]="meetingEdit.agenda" rows="2"></textarea>

            <div class="edit-actions">
              <button class="ghost-btn" (click)="closeMeetingEditor()">Cancel</button>
              <button class="primary-btn" (click)="saveMeetingEditor()">Save Changes</button>
            </div>
          </div>
        </div>

      </div>
    </div>
  `,
  styles: [`
    .chat-wrapper { flex: 1; display: flex; justify-content: center; background: #ffffff; color: #1e293b; height: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    .chat-container { width: 100%; max-width: 850px; display: flex; flex-direction: column; height: 100%; background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; }
    .messages { flex: 1; overflow-y: auto; padding: 25px; display: flex; flex-direction: column; gap: 24px; scroll-behavior: smooth; }
    .message-row { display: flex; flex-direction: column; width: 100%; }
    
    .msg { padding: 16px 20px; border-radius: 16px; max-width: 85%; font-size: 0.95rem; line-height: 1.6; white-space: pre-wrap; position: relative; }
    .user-msg { background: #2563eb; color: white; align-self: flex-end; border-bottom-right-radius: 4px; box-shadow: 0 10px 15px -3px rgba(37, 99, 235, 0.2); }
    .assistant-msg { background: #f8fafc; border: 1px solid #e2e8f0; color: #334155; align-self: flex-start; border-bottom-left-radius: 4px; }
    
    .edit-action { background: none; border: none; color: #64748b; cursor: pointer; font-size: 0.75rem; font-weight: 600; padding: 4px 8px; margin-top: 8px; transition: color 0.2s; }
    .edit-action:hover { color: #2563eb; }

    .join-box { background: #f1f5f9; border-radius: 12px; padding: 14px 18px; margin: 12px 0; border: 1px solid #e2e8f0; }
    .join-box b { color: #1e293b; display: block; margin-bottom: 4px; }
    .join-box a { color: #2563eb; font-weight: 600; text-decoration: none; word-break: break-all; }
    .join-box a:hover { text-decoration: underline; }

    .input-area { padding: 20px 25px; background: #ffffff; border-top: 1px solid #f1f5f9; display: flex; gap: 12px; align-items: center; }
    .input-area input { flex: 1; padding: 14px 22px; border-radius: 12px; border: 1px solid #e2e8f0; background: #fcfcfc; font-size: 1rem; outline: none; transition: all 0.2s; color: #1e293b; }
    .input-area input:focus { border-color: #2563eb; background: #ffffff; box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.08); }
    
    .send-btn { background: #2563eb; color: white; border: none; width: 48px; height: 48px; border-radius: 12px; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.2s; }
    .send-btn:hover:not(:disabled) { background: #1d4ed8; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2); }
    .send-btn:disabled { background: #cbd5e1; cursor: not-allowed; }

    .thinking-wrapper { display: flex; align-items: center; gap: 12px; padding: 10px; color: #64748b; font-size: 0.9rem; font-weight: 500; }
    .spinner-rotate { width: 20px; height: 20px; color: #2563eb; animation: spin 1s linear infinite; }

    .card-wrapper { margin-top: 16px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }
    .section-header { font-size: 0.75rem; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 14px; }
    
    .stacked-buttons { display: flex; flex-direction: column; gap: 10px; }
    .choice-btn { 
      background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 18px; 
      font-size: 0.95rem; font-weight: 500; color: #475569; text-align: left; cursor: pointer; transition: all 0.2s; 
    }
    .choice-btn:hover { border-color: #2563eb; background: #f0f7ff; color: #2563eb; }
    .choice-btn.selected { border-color: #2563eb; background: #eff6ff; color: #2563eb; font-weight: 600; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1); }

    .multi-chip-area { display: flex; flex-wrap: wrap; gap: 10px; }
    .chip-btn { padding: 8px 16px; border-radius: 20px; border: 1px solid #e2e8f0; background: #ffffff; font-size: 0.9rem; font-weight: 500; color: #475569; cursor: pointer; transition: all 0.2s; }
    .chip-btn:hover { border-color: #2563eb; color: #2563eb; background: #f0f7ff; }
    .chip-btn.chip-selected { background: #2563eb; border-color: #2563eb; color: #ffffff; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2); }
    .multi-hint { font-size: 0.75rem; color: #94a3b8; margin-top: 10px; font-weight: 500; }

    .primary-btn { background: #2563eb; color: white; border: none; padding: 14px 24px; border-radius: 10px; font-weight: 600; cursor: pointer; transition: all 0.2s; font-size: 0.95rem; }
    .primary-btn:hover:not(:disabled) { background: #1d4ed8; box-shadow: 0 4px 15px rgba(37, 99, 235, 0.25); }
    .primary-btn.full { width: 100%; }
    .primary-btn.small { padding: 8px 16px; font-size: 0.85rem; }
    .primary-btn:disabled { background: #cbd5e1; cursor: not-allowed; }

    .ghost-btn { background: #ffffff; border: 1px solid #e2e8f0; color: #475569; padding: 14px 24px; border-radius: 10px; font-weight: 600; cursor: pointer; transition: all 0.2s; font-size: 0.95rem; }
    .ghost-btn:hover { background: #f8fafc; color: #1e293b; border-color: #cbd5e1; }
    .ghost-btn.small { padding: 8px 16px; font-size: 0.85rem; }
    .ghost-btn.tiny { padding: 4px 10px; font-size: 0.75rem; border-radius: 6px; }

    .editor-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.2); backdrop-filter: blur(8px); display: flex; align-items: center; justify-content: center; z-index: 2000; }
    .editor-card { width: min(650px, 95vw); background: #ffffff; border-radius: 24px; padding: 32px; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.15); display: flex; flex-direction: column; gap: 20px; border: 1px solid #f1f5f9; }
    .editor-card h3 { margin: 0; font-size: 1.5rem; font-weight: 800; color: #0f172a; border-bottom: 1px solid #f1f5f9; padding-bottom: 20px; }
    
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .form-field { display: flex; flex-direction: column; gap: 8px; }
    .form-field label { font-size: 0.75rem; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.025em; }
    .form-field input, .form-field select, .editor-card textarea { padding: 12px 16px; border: 1px solid #e2e8f0; border-radius: 10px; font-size: 0.95rem; outline: none; transition: all 0.2s; color: #1e293b; background: #fcfcfc; }
    .form-field input:focus, .form-field select:focus, .editor-card textarea:focus { border-color: #2563eb; background: #ffffff; box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.08); }

    .edit-actions { display: flex; gap: 14px; justify-content: flex-end; margin-top: 10px; }
    .mt-1 { margin-top: 16px; }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

    .disambig-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 20px; padding: 24px; margin-top: 16px; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05); }
    .disambig-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .disambig-title { font-weight: 800; font-size: 0.85rem; text-transform: uppercase; color: #64748b; letter-spacing: 0.05em; }
    .person-card { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border: 1px solid #f1f5f9; border-radius: 14px; cursor: pointer; text-align: left; width: 100%; background: #ffffff; transition: all 0.2s; }
    .person-card:hover { border-color: #2563eb; background: #f0f7ff; }
    .person-card.person-selected { border-color: #2563eb; background: #eff6ff; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1); }
    .person-avatar { width: 40px; height: 40px; border-radius: 12px; background: #eff6ff; color: #2563eb; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 1rem; }
    .person-name { font-size: 1rem; font-weight: 700; color: #0f172a; }
    .person-dept { font-size: 0.8rem; color: #64748b; font-weight: 500; }
  `]
})
export class ChatComponent {
  api = inject(ApiService);
  cdr = inject(ChangeDetectorRef);
  
  @ViewChild('scrollContainer') scrollContainer!: ElementRef;
  private readonly sessionStorageKey = 'ai-butler-session-id';
  private sessionId = this.getOrCreateSessionId();

  prompt = '';
  loading = false;
  loadingMessage = 'AI is thinking';
  editingIndex: number | null = null;
  editPrompt = '';
  private statusPollSub?: Subscription;

  messages: Message[] = [
    {
      role: 'assistant',
      content: '👋 Hi! I am ARIA, your AI Scheduling Assistant.\n\nJust tell me what you need — for example:\n• "Schedule a 1:1 with Anand"\n• "Book a sprint review with the engineering team on Friday"\n• "Meet with Rahul and Ananya tomorrow at 3pm"\n\nI will handle time slots, rooms, title, and agenda automatically.'
    }
  ];
  
  // For attendee selection state
  selectedAttendee: string = '';
  selectedAttendees: string[] = [];
  confirmCandidateChoice = '';
  showMeetingEditor = false;
  meetingEdit: any = {};

  // ── Disambiguation state ─────────────────────────────────────────────────
  disambigBulkMode = false;          // false = single select, true = multi-select
  selectedDisambigPeople = new Set<string>(); // selected EIDs
  private disambigCache = new Map<string, DisambigPerson>(); // label → parsed person

  dragX = 0;
  dragY = 0;
  isDragging = false;
  startX = 0;
  startY = 0;

  constructor() {}

  onDragStart(event: MouseEvent) {
    this.isDragging = true;
    this.startX = event.clientX - this.dragX;
    this.startY = event.clientY - this.dragY;
    event.preventDefault();
  }

  onDrag(event: MouseEvent) {
    if (!this.isDragging) return;
    this.dragX = event.clientX - this.startX;
    this.dragY = event.clientY - this.startY;
  }

  onDragEnd() {
    this.isDragging = false;
  }

  // ─── Gathering Card local state ──────────────────────────────────────────
  // Maps sectionKey → selected option label. Populated purely on the frontend;
  // only serialised and sent to backend when 'Confirm & Book' is tapped.
  cardSelections: Record<string, string> = {};

  // For multi-select sections (presenter): sectionKey → Set of selected labels
  multiSelections: Record<string, Set<string>> = {};

  // ─── Card Helpers ────────────────────────────────────────────────────────

  /** True if this section allows multi-select (presenter) */
  isMultiSection(section: string): boolean {
    return section.toLowerCase().includes('(multi)');
  }

  /** True if this section is a free text input */
  isTextSection(section: string): boolean {
    return section.toLowerCase().includes('(type below)');
  }

  /** Handle text input typing */
  onTextInput(section: string, event: Event) {
    const val = (event.target as HTMLInputElement).value;
    this.cardSelections[section] = val;
    this.cdr.detectChanges();
  }

  /** True if a chip is selected in the multi-select area */
  isChipSelected(section: string, opt: string): boolean {
    return !!this.multiSelections[section]?.has(this.cleanOpt(opt));
  }

  /** Toggle a chip selection for multi-select sections */
  onChipToggle(section: string, opt: string, msg: Message) {
    const label = this.cleanOpt(opt);
    if (!this.multiSelections[section]) {
      this.multiSelections[section] = new Set();
    }
    if (this.multiSelections[section].has(label)) {
      this.multiSelections[section].delete(label);
    } else {
      this.multiSelections[section].add(label);
    }
    // Reflect in cardSelections as comma-joined string
    this.cardSelections[section] = Array.from(this.multiSelections[section]).join(', ');
    this.cdr.detectChanges();
  }

  // ─── Card Helpers ────────────────────────────────────────────────────────

  /** Return ordered keys of an object (for *ngFor iteration) */
  objectKeys(obj: any): string[] {
    return obj ? Object.keys(obj) : [];
  }

  /** Strip ✅ prefix reliably regardless of emoji code-unit width */
  cleanOpt(opt: string): string {
    return opt.startsWith('✅ ') ? opt.replace('✅ ', '') : opt;
  }

  /** Filter out any blank/whitespace-only entries from a section's option array */
  filterOpts(opts: any[]): string[] {
    return (opts || []).filter((o: string) => o && o.trim().length > 0);
  }

  /**
   * When user taps a button inside a gathering card section:
   * - mark that option with ✅ locally (so the card updates without a server round-trip)
   * - strip ✅ from all OTHER options in the same section
   * - then call sendAction with a compact spec so the backend can also update draft state
   */
  onCardTap(sectionKey: string, opt: string, msg: Message) {
    const raw = this.cleanOpt(opt);
    if (!msg.titledSections) return;

    // If tapping the already-selected option — deselect it
    const isAlreadySelected = opt.startsWith('✅');
    if (isAlreadySelected) {
      delete this.cardSelections[sectionKey];
      // Strip the ✅ from this option (deselect)
      msg.titledSections[sectionKey] = (msg.titledSections[sectionKey] as string[]).map(
        (o: string) => this.cleanOpt(o)
      );
      this.cdr.detectChanges();
      return;
    }

    // Store in local state (never call backend here)
    this.cardSelections[sectionKey] = raw;

    // Mark selected, unmark all others in this section
    msg.titledSections[sectionKey] = (msg.titledSections[sectionKey] as string[]).map((o: string) => {
      const clean = this.cleanOpt(o);
      return clean === raw ? `✅ ${clean}` : clean;
    });
    this.cdr.detectChanges();
    // ⚠️ Do NOT call sendAction here — that would displace the card.
  }

  /** Handle conflict card tap — alternate slot or proceed original */
  onConflictTap(opt: string) {
    if (opt.startsWith('Proceed with original')) {
      this.sendAction('Continue with given time anyway');
    } else {
      this.sendAction('Book slot: ' + opt);
    }
  }

  /**
   * Called when user taps Confirm & Book.
   * Serialises all section selections into ONE structured message and sends it.
   * Clears cardSelections so the next card starts fresh.
   */
  onConfirmCard(msg: Message) {
    // Build the machine payload (not shown to user)
    const parts: string[] = ['[CONFIRM_BOOKING]'];
    for (const [section, value] of Object.entries(this.cardSelections)) {
      parts.push(`${section}=${value}`);
    }
    const payload = parts.join(' | ');
    this.cardSelections = {}; // reset for next card
    this.multiSelections = {}; // reset multi-select too

    // Show a clean label in the chat (not the raw payload)
    this.messages.push({ role: 'user', content: '✅ Confirming booking...' });
    this.scrollToBottom();

    // Send the machine payload to backend directly (without adding it to chat again)
    this.processQuery(payload);
  }

  isGatheringComplete(msg: Message): boolean {
    if (!msg.titledSections) return true;
    for (const key in msg.titledSections) {
      if (!msg.titledSections.hasOwnProperty(key)) continue;
      
      if (this.isTextSection(key)) {
        if (!this.cardSelections[key] || !this.cardSelections[key].trim()) return false;
      } else if (this.isMultiSection(key)) {
        // Multi-select: need at least one chip selected
        if (!this.cardSelections[key] || !this.cardSelections[key].trim()) return false;
      } else {
        const hasSelection = (msg.titledSections[key] || []).some((opt: string) => opt.startsWith('✅'));
        if (!hasSelection) return false;
      }
    }
    return true;
  }

  handleEditTapped(option: string, meetingData: any) {
    if (option.includes('Cancel')) {
      this.sendAction('Cancel meeting');
      return;
    }
    // Most edit buttons redirect to the dedicated meeting editor for precise control
    this.openMeetingEditor(meetingData);
    this.messages.push({ 
      role: 'assistant', 
      content: `Opening editor for ${option.replace('Edit ', '')}...` 
    });
    this.scrollToBottom();
  }

  sendMessage() {
    if (!this.prompt.trim()) return;
    const userText = this.prompt;
    this.prompt = '';
    
    this.messages.push({ role: 'user', content: userText });
    this.scrollToBottom();

    this.processQuery(userText);
  }

  sendAction(actionText: string) {
    this.messages.push({ role: 'user', content: actionText });
    this.scrollToBottom();
    this.processQuery(actionText);
  }

  startEdit(index: number, content: string) {
    this.editingIndex = index;
    this.editPrompt = content;
  }

  cancelEdit() {
    this.editingIndex = null;
    this.editPrompt = '';
  }

  saveEdit(index: number) {
    const newContent = this.editPrompt.trim();
    if (!newContent) return;
    
    // Remove all subsequent messages (ChatGPT style)
    this.messages.splice(index);
    this.messages.push({ role: 'user', content: newContent });
    this.editingIndex = null;
    this.editPrompt = '';
    
    this.processQuery(newContent);
  }

  processQuery(q: string) {
    this.loading = true;
    this.loadingMessage = 'AI is understanding your request';
    this.startStatusPolling();
    this.scrollToBottom();

    this.api.processMessage(q, this.sessionId).subscribe({
      next: (res) => {
        this.loading = false;
        this.stopStatusPolling();
        this.loadingMessage = 'AI is thinking';
        
        // Extract teams links
        const linkRegex = /https:\/\/teams\.\S+/g;
        const links = res.response.match(linkRegex) || [];
        
        const isInteractive = (res.options && res.options.length > 0) ||
                              res.option_type === 'duplicate_action' ||
                              res.option_type === 'title_and_agenda' ||
                              res.option_type === 'attendee_confirm' ||
                              res.option_type === 'timeslot' ||
                              res.option_type === 'conflict' ||
                              res.option_type === 'attendee';
        
        // Reset disambiguation state for each new response
        this.selectedDisambigPeople.clear();
        this.disambigCache.clear();
        this.disambigBulkMode = false;

        this.messages.push({
          role: 'assistant',
          content: res.response,
          links: links,
          options: res.options || [],
          optionType: res.option_type || 'general',
          titledSections: res.titled_sections || {},
          existingMeeting: res.existing_meeting || null,
          meetingData: res.meeting_data || null,
          candidateOptions: res.candidate_options || [],
          selectionMap: res.selection_map || {},
          isInteractive,
          intent: res.intent || ''
        });

        // Setup attendee state if needed
        if (res.option_type === 'attendee') {
           this.selectedAttendees = [];
        }
        if (res.option_type === 'attendee_confirm') {
          this.confirmCandidateChoice = (res.candidate_options || [])[0] || '';
        }

        this.scrollToBottom();
      },
      error: (err) => {
        this.loading = false;
        this.stopStatusPolling();
        this.loadingMessage = 'AI is thinking';
        this.messages.push({ role: 'assistant', content: '❌ Error contacting AI: ' + err.message });
        this.scrollToBottom();
      }
    });
  }

  deleteDuplicate(existingMeeting: any) {
    if (!existingMeeting) return;
    const fingerprint = existingMeeting.fingerprint || existingMeeting._fingerprint || '';
    const eventId = existingMeeting.event_id || '';
    if (!fingerprint && !eventId) {
      this.messages.push({ role: 'assistant', content: '❌ Could not find meeting id to delete. Please retry.' });
      this.scrollToBottom();
      return;
    }
    this.api.deleteMeeting(fingerprint, eventId).subscribe({
      next: () => this.sendAction(`Meeting cancelled and deleted. event_id=${eventId}; fingerprint=${fingerprint}`),
      error: (err) => {
        this.messages.push({ role: 'assistant', content: '❌ Failed to delete meeting: ' + err.message });
        this.scrollToBottom();
      }
    });
  }

  startDuplicateUpdate(existingMeeting: any) {
    if (!existingMeeting) {
      this.sendAction('I want to update this meeting with new time or details.');
      return;
    }
    const fingerprint = existingMeeting.fingerprint || existingMeeting._fingerprint || '';
    const eventId = existingMeeting.event_id || '';
    this.sendAction(
      `Please update existing meeting. event_id=${eventId}; fingerprint=${fingerprint}. ` +
      `I will provide new time/details now.`
    );
  }

  confirmAttendees(options: string[]) {
    // Filter out any blank entries and send confirmed attendees as action
    const selected = options.filter(opt => opt && opt.trim().length > 0);
    if (selected.length > 0) {
       const lines = selected.map((opt) => opt.split('*').join(''));
       this.sendAction(lines.join('\n'));
    }
  }

  confirmSingleAttendeeDropdown() {
    if (this.selectedAttendee) {
       this.sendAction(`${this.selectedAttendee} [required]`);
       this.selectedAttendee = '';
    }
  }

  confirmSelectedCandidate(selectionMap: Record<string, string>) {
    const eid = selectionMap[this.confirmCandidateChoice];
    if (!eid) {
      this.sendAction('Yes, proceed');
      return;
    }
    this.sendAction(`Select attendee: ${eid}`);
  }

  openMeetingEditor(meetingData: any) {
    const start = new Date(meetingData?.start || new Date().toISOString());
    const end = new Date(meetingData?.end || new Date(start.getTime() + 60 * 60000));
    const duration = Math.max(30, Math.round((end.getTime() - start.getTime()) / 60000));
    
    // Extract attendee names for the presenter dropdown
    const attendees = meetingData?.attendees || [];
    const attendeeNames = attendees.map((a: any) => typeof a === 'string' ? a : (a.name || a.displayName || a.id));

    this.meetingEdit = {
      event_id: meetingData?.event_id || '',
      fingerprint: meetingData?.fingerprint || '',
      subject: meetingData?.subject || '',
      agenda: meetingData?.agenda || '',
      location: meetingData?.location || 'Virtual',
      presenter: meetingData?.presenter || '',
      recurrence: meetingData?.recurrence || 'none',
      recurrence_end_date: meetingData?.recurrence_end_date || '',
      attendeeNames,
      date: start.toISOString().slice(0, 10),
      time: `${start.getHours().toString().padStart(2, '0')}:${start.getMinutes().toString().padStart(2, '0')}`,
      duration,
    };
    this.showMeetingEditor = true;
  }

  closeMeetingEditor() {
    this.showMeetingEditor = false;
    this.meetingEdit = {};
    this.dragX = 0;
    this.dragY = 0;
  }

  saveMeetingEditor() {
    if (!this.meetingEdit?.event_id || !this.meetingEdit?.date || !this.meetingEdit?.time) return;
    const localStart = new Date(`${this.meetingEdit.date}T${this.meetingEdit.time}:00`);
    const localEnd = new Date(localStart.getTime() + Number(this.meetingEdit.duration || 60) * 60000);
    this.api.updateMeeting({
      event_id: this.meetingEdit.event_id,
      fingerprint: this.meetingEdit.fingerprint || '',
      new_start: localStart.toISOString(),
      new_end: localEnd.toISOString(),
      new_subject: this.meetingEdit.subject || '',
      new_agenda: this.meetingEdit.agenda || '',
      new_location: this.meetingEdit.location || '',
      new_recurrence: this.meetingEdit.recurrence || 'none',
      new_presenter: this.meetingEdit.presenter || '',
    }).subscribe({
      next: () => {
        this.closeMeetingEditor();
        this.messages.push({ role: 'assistant', content: 'Meeting updated successfully.' });
        this.scrollToBottom();
      },
      error: (err) => {
        this.messages.push({ role: 'assistant', content: '❌ Failed to update meeting: ' + err.message });
        this.scrollToBottom();
      }
    });
  }

  formatMessage(text: string): string {
    // Bold
    let html = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Code inline
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Preserve box drawing / monospace blocks (lines starting with │ ┌ ┐ └ ┘ ╔ ╠ ╚ ╗ ╝ ║ ━ ─ ┼)
    const lines = html.split('\n');
    const processed = lines.map(line => {
      if (/^[│┌┐└┘╔╠╚╗╝║━─┼├┤┬┴╣╦╩╬\s]/.test(line) && line.trim().length > 0) {
        return `<span class="box-line">${line}</span>`;
      }
      return line;
    });
    return processed.join('<br>');
  }

  /** Returns true if the option is the 'Continue with given time' fallback. */
  isContinueOption(opt: string): boolean {
    const lower = opt.toLowerCase();
    return lower.includes('continue with given time') || lower.includes('proceed with given time');
  }

  regenerate(index: number) {
    if (index === 0) return;
    const prevMsg = this.messages[index - 1];
    if (prevMsg.role !== 'user') return;

    const userText = prevMsg.content;
    // Remove the current AI response and everything after
    this.messages.splice(index);
    this.processQuery(userText);
  }

  scrollToBottom() {
    setTimeout(() => {
      if (this.scrollContainer) {
        this.scrollContainer.nativeElement.scrollTop = this.scrollContainer.nativeElement.scrollHeight;
      }
    }, 50);
  }

  // ── Disambiguation helpers ──────────────────────────────────────────────

  /**
   * Parse raw option strings like "Select: John Doe (Engineering) - EID: 101"
   * into structured DisambigPerson objects. Results are cached.
   */
  getDisambigPeople(options: string[]): DisambigPerson[] {
    return options.map(label => {
      if (this.disambigCache.has(label)) return this.disambigCache.get(label)!;
      // Format: "Select: Name (Dept) - EID: 123"
      const m = label.match(/Select:\s*(.+?)\s*\((.+?)\)\s*-\s*EID:\s*(\w+)/i);
      const person: DisambigPerson = {
        label,
        name: m ? m[1].trim() : label,
        dept: m ? m[2].trim() : '',
        eid: m ? m[3].trim() : '',
        selected: false,
      };
      this.disambigCache.set(label, person);
      return person;
    });
  }

  isPersonSelected(p: DisambigPerson): boolean {
    return this.selectedDisambigPeople.has(p.eid);
  }

  onPersonTap(p: DisambigPerson, msg: Message) {
    if (!this.disambigBulkMode) {
      // Single mode: clear all and select this one only
      this.selectedDisambigPeople.clear();
      this.selectedDisambigPeople.add(p.eid);
    } else {
      // Bulk mode: toggle
      if (this.selectedDisambigPeople.has(p.eid)) {
        this.selectedDisambigPeople.delete(p.eid);
      } else {
        this.selectedDisambigPeople.add(p.eid);
      }
    }
    this.cdr.detectChanges();
  }

  toggleDisambigMode() {
    this.disambigBulkMode = !this.disambigBulkMode;
    if (!this.disambigBulkMode && this.selectedDisambigPeople.size > 1) {
      // Switching back to single — keep only the last selected
      const last = Array.from(this.selectedDisambigPeople).at(-1)!;
      this.selectedDisambigPeople.clear();
      this.selectedDisambigPeople.add(last);
    }
    this.cdr.detectChanges();
  }

  confirmDisambig(msg: Message) {
    if (this.selectedDisambigPeople.size === 0) return;
    const eids = Array.from(this.selectedDisambigPeople);
    // Build a human-readable confirmation with names
    const people = this.getDisambigPeople(msg.options || []);
    const names = eids.map(eid => {
      const p = people.find(x => x.eid === eid);
      return p ? `${p.name} (EID: ${eid})` : `EID: ${eid}`;
    });
    const actionText = `Select attendee: ${eids.join(',')} | ${names.join(', ')}`;
    this.selectedDisambigPeople.clear();
    this.disambigCache.clear();
    this.sendAction(actionText);
  }

  clearChat() {
    this.messages = [];
  }

  addAssistantMessage(content: string) {
    this.messages.push({ role: 'assistant', content });
    this.scrollToBottom();
  }

  private getOrCreateSessionId(): string {
    const fallback = this.generateSessionId();

    try {
      const existing = window.localStorage.getItem(this.sessionStorageKey);
      if (existing) {
        return existing;
      }

      window.localStorage.setItem(this.sessionStorageKey, fallback);
      return fallback;
    } catch {
      return fallback;
    }
  }

  private generateSessionId(): string {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }

    return `session-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  private startStatusPolling() {
    this.stopStatusPolling();
    this.statusPollSub = interval(600).subscribe(() => {
      this.api.getAgentStatus(this.sessionId).subscribe({
        next: (res) => {
          if (res?.message) {
            this.loadingMessage = res.message;
            this.cdr.markForCheck();
          }
        }
      });
    });
  }

  private stopStatusPolling() {
    this.statusPollSub?.unsubscribe();
    this.statusPollSub = undefined;
  }
}
