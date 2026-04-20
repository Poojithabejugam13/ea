import { Component, ChangeDetectorRef, ViewChild, ElementRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api';
import { interval, Subscription } from 'rxjs';

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
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="chat-wrapper">
      <div class="chat-container">
        
        <div class="messages" #scrollContainer>
          <div *ngFor="let msg of messages; let i = index" class="message-row">
            <div [class]="msg.role === 'user' ? 'msg user-msg' : 'msg assistant-msg'">
              <ng-container *ngIf="editingIndex !== i; else editTpl">
                <div class="msg-content" [innerHTML]="formatMessage(msg.content)"></div>
                <button *ngIf="msg.role === 'user'" class="edit-btn" (click)="startEdit(i, msg.content)" title="Edit message">
                  ✎ Edit
                </button>
                <button *ngIf="msg.role === 'assistant' && i > 0" class="regenerate-btn" (click)="regenerate(i)" title="Regenerate response">
                  ↻ Resend
                </button>
                <button *ngIf="msg.role === 'assistant' && msg.meetingData" class="edit-btn" (click)="openMeetingEditor(msg.meetingData)" title="Edit meeting">
                  ✎ Edit Meeting
                </button>
              </ng-container>
              
              <ng-template #editTpl>
                <div class="edit-area">
                  <textarea [(ngModel)]="editPrompt" rows="3"></textarea>
                  <div class="edit-actions">
                    <button class="save-btn" (click)="saveEdit(i)">Save & Submit</button>
                    <button class="cancel-btn" (click)="cancelEdit()">Cancel</button>
                  </div>
                </div>
              </ng-template>
              
              <!-- Join Links -->
              <div *ngFor="let link of msg.links" class="join-box">
                🔗 <b>Join Meeting Link</b><br>
                <a [href]="link" target="_blank">{{link}}</a>
              </div>

              <!-- Interactive Buttons (only show for the LAST message if it's assistant) -->
              <div *ngIf="msg.isInteractive && i === messages.length - 1" class="interactive-area">
                
                <!-- TITLE + AGENDA (Combined) -->
                <ng-container *ngIf="msg.optionType === 'title_and_agenda'">
                  
                  <div *ngIf="msg.titledSections?.titles?.length > 0">
                    <p class="tap-label">Tap to choose a title</p>
                    <div class="options-grid">
                      <button *ngFor="let t of msg.titledSections.titles" class="opt-btn" (click)="sendAction('Use title: ' + t)">{{t}}</button>
                    </div>
                  </div>

                  <div *ngIf="msg.titledSections?.agendas?.length > 0">
                    <p class="tap-label">Tap to choose an agenda</p>
                    <div class="options-grid">
                      <button *ngFor="let a of msg.titledSections.agendas" class="opt-btn" (click)="sendAction('Use agenda: ' + a)">{{a}}</button>
                    </div>
                  </div>

                </ng-container>

                <!-- DUPLICATE ACTIONS -->
                <ng-container *ngIf="msg.optionType === 'duplicate_action'">
                  <div class="dup-grid">
                    <button class="dup-update" (click)="startDuplicateUpdate(msg.existingMeeting)">🔄 Update time / details</button>
                    <button class="dup-del" (click)="deleteDuplicate(msg.existingMeeting)">🗑️ Cancel & delete</button>
                    <button class="dup-new" (click)="sendAction('Book this as a completely new separate meeting, ignore the existing one.')">➕ Book as new separate meeting</button>
                  </div>
                </ng-container>

                <!-- ATTENDEE SELECTION (Simplified multi-select emulation) -->
                <ng-container *ngIf="msg.optionType === 'attendee'">
                   <p class="tap-label">Select Attendees</p>
                   <div *ngFor="let opt of msg.options; let j=index" class="attendee-row">
                      <label>
                        <input type="checkbox" [(ngModel)]="attendeeSelections[j].selected">
                        {{opt}}
                      </label>
                      <select *ngIf="attendeeSelections[j].selected" [(ngModel)]="attendeeSelections[j].importance">
                        <option value="optional">Optional</option>
                        <option value="required">Required</option>
                      </select>
                   </div>
                   <button class="confirm-btn" (click)="confirmAttendees(msg.options || [])">✅ Confirm Attendees</button>
                </ng-container>

                <!-- ATTENDEE CONFIRMATION WITH DROPDOWN -->
                <ng-container *ngIf="msg.optionType === 'attendee_confirm'">
                  <p class="tap-label">Confirm selected person</p>
                  <div class="attendee-row">
                    <select [(ngModel)]="confirmCandidateChoice" style="width: 100%;">
                      <option *ngFor="let c of (msg.candidateOptions || [])" [value]="c">{{c}}</option>
                    </select>
                  </div>
                  <div class="options-grid">
                    <button class="opt-btn" (click)="confirmSelectedCandidate(msg.selectionMap || {})">Yes, proceed</button>
                  </div>
                </ng-container>

                <!-- GENERAL / TIMESLOT / TITLE / AGENDA -->
                <ng-container *ngIf="['title', 'agenda', 'timeslot', 'general', 'conflict'].includes(msg.optionType || '')">
                  <p class="tap-label">Tap to choose</p>
                  <div class="options-grid">
                    <button *ngFor="let opt of msg.options" class="opt-btn" (click)="sendAction(msg.optionType === 'timeslot' ? 'Book slot: '+opt : msg.optionType === 'title' ? 'Use title: '+opt : msg.optionType === 'agenda' ? 'Use agenda: '+opt : opt)">
                      {{opt.length <= 65 ? opt : (opt | slice:0:62) + '…'}}
                    </button>
                  </div>
                </ng-container>

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
              <span class="dots">
                <span>.</span><span>.</span><span>.</span>
              </span>
            </div>
          </div>
        </div>

        <div class="input-area">
          <input type="text" [(ngModel)]="prompt" (keyup.enter)="sendMessage()" placeholder="Type your response here...">
          <button class="send-btn" (click)="sendMessage()" [disabled]="!prompt.trim()">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
          </button>
        </div>

        <div *ngIf="showMeetingEditor" class="editor-overlay">
          <div class="editor-card">
            <h3>Edit Meeting</h3>
            <label>Title</label>
            <input [(ngModel)]="meetingEdit.subject" type="text">
            <label>Date</label>
            <input [(ngModel)]="meetingEdit.date" type="date">
            <label>Time</label>
            <input [(ngModel)]="meetingEdit.time" type="time">
            <label>Duration (minutes)</label>
            <input [(ngModel)]="meetingEdit.duration" type="number" min="30" step="15">
            <label>Agenda</label>
            <textarea [(ngModel)]="meetingEdit.agenda" rows="3"></textarea>
            <label>Location</label>
            <input [(ngModel)]="meetingEdit.location" type="text">
            <label>Presenter</label>
            <input [(ngModel)]="meetingEdit.presenter" type="text">
            <label>Recurrence</label>
            <input [(ngModel)]="meetingEdit.recurrence" type="text">
            <div class="edit-actions">
              <button class="cancel-btn" (click)="closeMeetingEditor()">Cancel</button>
              <button class="save-btn" (click)="saveMeetingEditor()">Save Changes</button>
            </div>
          </div>
        </div>

      </div>
    </div>
  `,
  styles: [`
    .chat-wrapper { flex: 1; display: flex; justify-content: center; background: #020617; color: #f8fafc; height: 100%; overflow: hidden; }
    .chat-container { width: 100%; max-width: 800px; display: flex; flex-direction: column; height: 100%; }
    .messages { flex: 1; overflow-y: auto; padding: 30px 20px; display: flex; flex-direction: column; gap: 20px; scroll-behavior: smooth;}
    .message-row { display: flex; flex-direction: column; width: 100%; }
    .msg { padding: 12px 18px; border-radius: 12px; max-width: 85%; font-size: 0.95rem; line-height: 1.5; white-space: pre-wrap; position: relative; }
    .user-msg { background: #1e293b; color: #f1f5f9; align-self: flex-end; border-bottom-right-radius: 2px; }
    .assistant-msg { background: rgba(30, 41, 59, 0.5); border: 1px solid rgba(255, 255, 255, 0.05); color: #cbd5e1; align-self: flex-start; max-width: 100%; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    ::ng-deep .assistant-msg h3 { margin-top: 0; }
    ::ng-deep .assistant-msg p { margin: 0 0 10px 0; }
    ::ng-deep .assistant-msg strong { color: #818cf8; }

    .join-box { background: linear-gradient(135deg, #0f172a, #1e293b); border: 1px solid #6366f1; border-radius: 12px; padding: 14px 20px; margin: 10px 0 6px 0; width: fit-content; }
    .join-box a { color: #818cf8; font-weight: 600; text-decoration: none; }
    .join-box a:hover { color: #a5b4fc; }

    .input-area { padding: 20px; background: #020617; border-top: 1px solid #1e293b; display: flex; gap: 12px; align-items: center; }
    input { flex: 1; padding: 14px 20px; border-radius: 12px; border: 1px solid #334155; background: #0f172a; color: white; font-size: 1rem; outline: none; transition: 0.2s; }
    input:focus { border-color: #6366f1; }
    
    .send-btn { 
      background: #6366f1; 
      color: white; 
      border: none; 
      width: 48px; height: 48px; 
      border-radius: 12px; 
      display: flex; align-items: center; justify-content: center; 
      cursor: pointer; transition: 0.2s; 
    }
    .send-btn:hover:not(:disabled) { background: #818cf8; transform: scale(1.05); }
    .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    
    .thinking-wrapper { display: flex; align-items: center; gap: 12px; margin-top: 15px; padding-left: 10px; }
    .spinner-rotate { width: 22px; height: 22px; color: #6366f1; animation: spin 1s linear infinite; }
    .thinking-text { font-size: 0.9rem; color: #94a3b8; font-weight: 500; }
    .dots span { animation: pulse 1.4s infinite; opacity: 0; }
    .dots span:nth-child(2) { animation-delay: 0.2s; }
    .dots span:nth-child(3) { animation-delay: 0.4s; }

    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    @keyframes pulse { 0% { opacity: 0; } 50% { opacity: 1; } 100% { opacity: 0; } }

    .edit-btn { 
      background: rgba(255, 255, 255, 0.05); 
      border: 1px solid rgba(255, 255, 255, 0.1); 
      color: #94a3b8; 
      cursor: pointer; 
      padding: 4px 10px; 
      border-radius: 6px;
      font-size: 0.75rem; 
      font-weight: 500;
      transition: all 0.2s; 
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 8px;
    }
    .edit-btn:hover { background: rgba(99, 102, 241, 0.2); border-color: #6366f1; color: white; }

    .edit-area { display: flex; flex-direction: column; gap: 8px; width: 100%; min-width: 300px; }
    .edit-area textarea { background: #0f172a; border: 1px solid #334155; color: white; padding: 10px; border-radius: 8px; font-family: inherit; resize: vertical; }
    .edit-actions { display: flex; gap: 8px; justify-content: flex-end; }
    .save-btn { background: #6366f1; color: white; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
    .cancel-btn { background: none; border: 1px solid #334155; color: #94a3b8; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }

    .regenerate-btn { 
      background: rgba(255, 255, 255, 0.05); 
      border: 1px solid rgba(255, 255, 255, 0.1); 
      color: #94a3b8; 
      cursor: pointer; 
      padding: 4px 10px; 
      border-radius: 6px;
      font-size: 0.75rem; 
      font-weight: 500;
      transition: all 0.2s; 
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 10px;
    }
    .regenerate-btn:hover { background: rgba(99, 102, 241, 0.2); border-color: #6366f1; color: white; }

    .interactive-area { margin-top: 15px; }
    .tap-label { color: #94a3b8; font-size: 0.78rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 8px; margin-top: 15px; }
    
    .options-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
    .opt-btn { 
      background: rgba(30, 41, 59, 0.4); 
      color: #f1f5f9; 
      border: 1px solid rgba(255, 255, 255, 0.1); 
      border-radius: 14px; 
      padding: 14px 18px; 
      font-size: 0.95rem; 
      font-weight: 500; 
      cursor: pointer; 
      text-align: left; 
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      backdrop-filter: blur(8px);
    }
    .opt-btn:hover { background: rgba(99, 102, 241, 0.2); border-color: #6366f1; color: white; transform: translateY(-2px); box-shadow: 0 8px 16px rgba(99, 102, 241, 0.3); }

    .dup-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
    .dup-update { background: linear-gradient(135deg, #0f4c75, #1b6ca8); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-del { background: linear-gradient(135deg, #7f1d1d, #b91c1c); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-new { background: linear-gradient(135deg, #14532d, #15803d); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    
    .attendee-row { display: flex; align-items: center; justify-content: space-between; background: #1e293b; padding: 10px 15px; border-radius: 8px; margin-bottom: 8px; }
    .attendee-row label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
    .attendee-row select { background: #0f172a; border: 1px solid #334155; color: white; padding: 6px; border-radius: 6px; outline: none; }
    .confirm-btn { background: linear-gradient(135deg, #6366f1, #4f46e5); color: white; border: none; border-radius: 8px; padding: 12px 20px; font-weight: 600; cursor: pointer; width: 100%; margin-top: 10px; }
    .editor-overlay { position: fixed; inset: 0; background: rgba(2,6,23,0.75); display: flex; align-items: center; justify-content: center; z-index: 2000; }
    .editor-card { width: min(560px, 90vw); background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
    .editor-card h3 { margin: 0 0 8px 0; color: #f8fafc; }
    .editor-card label { font-size: 0.8rem; color: #94a3b8; }
    .editor-card textarea, .editor-card input { background: #020617; border: 1px solid #334155; color: #fff; border-radius: 8px; padding: 10px; }
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
      content: '👋 **Hello! I am your AI Scheduling Assistant.**\n\nYou can either use the **Schedule Meeting** button above to fill in the details, or just tell me what you need right here!'
    }
  ];
  
  // For attendee selection state
  attendeeSelections: {selected: boolean, importance: string}[] = [];
  confirmCandidateChoice = '';
  showMeetingEditor = false;
  meetingEdit: any = {};

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
                              res.option_type === 'attendee_confirm';
        
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
          isInteractive
        });

        // Setup attendee state if needed
        if (res.option_type === 'attendee') {
           this.attendeeSelections = (res.options || []).map(() => ({
             selected: false,
             importance: q.toLowerCase().includes('imp') ? 'required' : 'optional'
           }));
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
    const selected = options.filter((_, i) => this.attendeeSelections[i].selected);
    if (selected.length > 0) {
       const lines = selected.map((opt, i) => {
         const ogIndex = options.indexOf(opt);
         return `${opt} [${this.attendeeSelections[ogIndex].importance}]`;
       });
       this.sendAction(lines.join('\n'));
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
    this.meetingEdit = {
      event_id: meetingData?.event_id || '',
      fingerprint: meetingData?.fingerprint || '',
      subject: meetingData?.subject || '',
      agenda: meetingData?.agenda || '',
      location: meetingData?.location || 'Virtual',
      presenter: meetingData?.presenter || '',
      recurrence: meetingData?.recurrence || 'none',
      date: start.toISOString().slice(0, 10),
      time: `${start.getHours().toString().padStart(2, '0')}:${start.getMinutes().toString().padStart(2, '0')}`,
      duration,
    };
    this.showMeetingEditor = true;
  }

  closeMeetingEditor() {
    this.showMeetingEditor = false;
    this.meetingEdit = {};
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

  formatMessage(text: string) {
    // Basic markdown fake implementation for bold
    let html = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    return html;
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
