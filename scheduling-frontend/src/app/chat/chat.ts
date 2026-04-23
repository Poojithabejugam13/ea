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
                            <span class="chip-check" *ngIf="isChipSelected(section, opt)">✓</span>
                            {{ cleanOpt(opt) }}
                          </button>
                        </div>
                        <p class="multi-hint">Select one or more · tap Everyone to let any participant present</p>
                      </ng-container>

                      <!-- SINGLE-SELECT radio-ring buttons -->
                      <ng-container *ngIf="!isMultiSection(section) && !isTextSection(section)">
                        <div class="stacked-buttons">
                          <button 
                            *ngFor="let opt of filterOpts(msg.titledSections[section])" 
                            class="choice-btn"
                            [class.selected]="opt.startsWith('✅')"
                            (click)="onCardTap(section, opt, msg)"
                          >
                            <span class="radio-ring">
                              <span class="radio-dot" *ngIf="opt.startsWith('✅')"></span>
                            </span>
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
                      class="confirm-btn" 
                      [disabled]="!isGatheringComplete(msg)"
                      (click)="onConfirmCard(msg)"
                    >
                      ✅ Confirm & Book
                    </button>
                  </div>
                </div>

                <!-- CONFLICT: alternates stacked + keep-original button at bottom -->
                <div *ngIf="msg.optionType === 'conflict'" class="card-wrapper">
                  <p class="section-header">📅 Available Alternatives</p>
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

                <!-- EDIT GRID (Post-booking 2-column grid) -->
                <div *ngIf="msg.optionType === 'edit_grid'" class="edit-grid">
                  <button 
                    *ngFor="let opt of msg.options" 
                    class="edit-btn"
                    (click)="handleEditTapped(opt, msg.meetingData)"
                  >
                    {{ opt }}
                  </button>
                </div>

                <!-- DISAMBIGUATION CARD — rich person picker -->
                <div *ngIf="msg.intent === 'attendee_disambiguation' || msg.optionType === 'attendee_disambiguation'" class="disambig-card">
                  <div class="disambig-header">
                    <span class="disambig-title">👥 Select Attendee</span>
                    <button class="toggle-mode-btn" (click)="toggleDisambigMode()">
                      {{ disambigBulkMode ? '☑ Bulk Mode' : '◻ Single Mode' }}
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
                        <span class="person-eid">EID {{ p.eid }}</span>
                      </div>
                      <div *ngIf="isPersonSelected(p)" class="person-check">✓</div>
                    </button>
                  </div>
                  <button
                    class="disambig-confirm-btn"
                    [disabled]="selectedDisambigPeople.size === 0"
                    (click)="confirmDisambig(msg)"
                  >
                    {{ disambigBulkMode ? 'Add Selected (' + selectedDisambigPeople.size + ')' : 'Confirm Selection' }}
                  </button>
                </div>

                <!-- GENERAL (stacked fallback for any other option type) -->
                <div *ngIf="msg.intent !== 'attendee_disambiguation' && msg.optionType !== 'attendee_disambiguation' && !['gathering_card', 'edit_grid', 'conflict'].includes(msg.optionType || '') && (msg.options?.length || 0) > 0" class="stacked-buttons" style="margin-top: 12px;">
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
              <span class="dots">
                <span>.</span><span>.</span><span>.</span>
              </span>
            </div>
          </div>
        </div>

        <div class="input-area">
          <input 
            type="text" 
            [(ngModel)]="prompt" 
            (keyup.enter)="sendMessage()" 
            placeholder="Type your response here..."
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
    .assistant-msg { background: rgba(30, 41, 59, 0.5); border: 1px solid rgba(255, 255, 255, 0.05); color: #cbd5e1; align-self: flex-start; max-width: 85%; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    ::ng-deep .assistant-msg h3 { margin-top: 0; }
    ::ng-deep .assistant-msg p { margin: 0 0 10px 0; }
    ::ng-deep .assistant-msg strong { color: #818cf8; }

    /* ── Multi-select chip area ── */
    .multi-chip-area { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
    .chip-btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 6px 14px; border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.15);
      background: rgba(30, 41, 59, 0.5);
      color: #cbd5e1; font-size: 0.85rem; font-weight: 500;
      cursor: pointer; transition: all 0.18s;
    }
    .chip-btn:hover { border-color: #818cf8; color: #e0e7ff; background: rgba(99,102,241,0.15); }
    .chip-btn.chip-selected { background: rgba(99,102,241,0.25); border-color: #6366f1; color: #fff; }
    .chip-check { font-size: 0.75rem; color: #a5b4fc; }
    .multi-hint { font-size: 0.72rem; color: #64748b; margin-top: 6px; margin-bottom: 0; }

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
    .slot-btn { border-color: rgba(99, 102, 241, 0.35); background: rgba(99, 102, 241, 0.08); }
    .slot-btn:hover { background: rgba(99, 102, 241, 0.25); border-color: #818cf8; }
    .continue-btn { background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.45); color: #6ee7b7; border-radius: 14px; padding: 14px 18px; font-size: 0.95rem; font-weight: 600; cursor: pointer; text-align: left; transition: all 0.2s; }
    .continue-btn:hover { background: rgba(16, 185, 129, 0.25); border-color: #10b981; color: white; transform: translateY(-2px); box-shadow: 0 8px 16px rgba(16, 185, 129, 0.3); }
    ::ng-deep .box-line { font-family: 'Courier New', monospace; font-size: 0.85rem; color: #94a3b8; display: block; white-space: pre; letter-spacing: 0; }

    .dup-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
    .dup-update { background: linear-gradient(135deg, #0f4c75, #1b6ca8); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-del { background: linear-gradient(135deg, #7f1d1d, #b91c1c); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-new { background: linear-gradient(135deg, #14532d, #15803d); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    
    .attendee-dropdown {
      width: 100%;
      background: #0f172a;
      border: 1px solid #334155;
      color: #f1f5f9;
      padding: 8px;
      border-radius: 12px;
      font-size: 0.95rem;
      outline: none;
      transition: 0.2s;
    }
    .attendee-dropdown option {
      padding: 12px 16px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      cursor: pointer;
    }
    .attendee-dropdown option:checked {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.4), rgba(99, 102, 241, 0.2));
      color: white;
    }
    .attendee-dropdown:focus { border-color: #6366f1; }
    .help-text {
      font-size: 0.75rem;
      color: #64748b;
      margin: 6px 0 0 4px;
      font-style: italic;
    }
    .attendee-row { 
      display: flex; align-items: center; gap: 12px;
      background: rgba(30, 41, 59, 0.3); 
      border: 1px solid rgba(255, 255, 255, 0.05);
      padding: 12px 18px; 
      border-radius: 12px; 
      margin-bottom: 10px; 
      transition: all 0.2s;
    }
    .attendee-row:hover { background: rgba(99, 102, 241, 0.1); border-color: rgba(99, 102, 241, 0.3); }
    .attendee-row label { display: flex; align-items: center; gap: 12px; cursor: pointer; flex: 1; font-weight: 500; min-width: 0; }
    .attendee-row select { 
      background: #0f172a; 
      border: 1px solid #334155; 
      color: #94a3b8; 
      padding: 6px 10px; 
      border-radius: 8px; 
      outline: none; 
      font-size: 0.85rem;
      transition: 0.2s;
    }
    .attendee-row select:focus { border-color: #6366f1; color: white; }
    .confirm-btn { 
      background: linear-gradient(135deg, #6366f1, #4f46e5); 
      color: white; 
      border: none; 
      border-radius: 12px; 
      padding: 14px 20px; 
      font-weight: 600; 
      cursor: pointer; 
      width: 100%; 
      margin-top: 15px; 
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
      transition: 0.2s;
    }
    .confirm-btn:hover { transform: translateY(-1px); box-shadow: 0 6px 15px rgba(99, 102, 241, 0.4); }
    .editor-overlay { position: fixed; inset: 0; background: rgba(2,6,23,0.75); display: flex; align-items: center; justify-content: center; z-index: 2000; }
    .editor-card { width: min(560px, 90vw); background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
    .editor-card h3 { margin: 0 0 8px 0; color: #f8fafc; }
    .editor-card label { font-size: 0.8rem; color: #94a3b8; }
    .editor-card textarea, .editor-card input { background: #020617; border: 1px solid #334155; color: #fff; border-radius: 8px; padding: 10px; }

    /* ── Disambiguation Card ── */
    .disambig-card { margin-top: 14px; background: rgba(15,23,42,0.8); border: 1px solid rgba(99,102,241,0.3); border-radius: 16px; padding: 16px; }
    .disambig-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
    .disambig-title { font-size: 0.85rem; font-weight: 700; color: #a5b4fc; letter-spacing: 0.05em; text-transform: uppercase; }
    .toggle-mode-btn {
      font-size: 0.78rem; font-weight: 600; padding: 5px 12px; border-radius: 999px;
      border: 1px solid rgba(99,102,241,0.5); background: rgba(99,102,241,0.12); color: #a5b4fc;
      cursor: pointer; transition: all 0.18s;
    }
    .toggle-mode-btn:hover { background: rgba(99,102,241,0.25); color: #fff; }
    .disambig-grid { display: flex; flex-direction: column; gap: 8px; }
    .person-card {
      display: flex; align-items: center; gap: 12px;
      background: rgba(30,41,59,0.5); border: 1px solid rgba(255,255,255,0.07);
      border-radius: 12px; padding: 12px 14px; cursor: pointer;
      transition: all 0.18s; text-align: left; width: 100%;
      position: relative;
    }
    .person-card:hover { border-color: rgba(99,102,241,0.5); background: rgba(99,102,241,0.1); }
    .person-card.person-selected { border-color: #6366f1; background: rgba(99,102,241,0.18); }
    .person-avatar {
      width: 40px; height: 40px; border-radius: 50%;
      background: linear-gradient(135deg, #4f46e5, #7c3aed);
      display: flex; align-items: center; justify-content: center;
      font-size: 1rem; font-weight: 700; color: white; flex-shrink: 0;
    }
    .person-info { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
    .person-name { font-size: 0.92rem; font-weight: 600; color: #f1f5f9; }
    .person-dept {
      display: inline-block; font-size: 0.72rem; font-weight: 600;
      color: #818cf8; background: rgba(99,102,241,0.15);
      border: 1px solid rgba(99,102,241,0.3); border-radius: 999px;
      padding: 1px 8px; margin-top: 2px; width: fit-content;
    }
    .person-eid { font-size: 0.72rem; color: #475569; margin-top: 1px; }
    .person-check {
      position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
      width: 22px; height: 22px; border-radius: 50%;
      background: #6366f1; color: white; font-size: 0.8rem;
      display: flex; align-items: center; justify-content: center; font-weight: 700;
    }
    .disambig-confirm-btn {
      width: 100%; margin-top: 12px; padding: 11px;
      background: linear-gradient(135deg, #4f46e5, #6366f1);
      color: white; border: none; border-radius: 10px;
      font-weight: 700; font-size: 0.9rem; cursor: pointer; transition: 0.18s;
    }
    .disambig-confirm-btn:hover:not(:disabled) { opacity: 0.9; transform: translateY(-1px); }
    .disambig-confirm-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    
    .suggestion-dropdown {
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 12px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
      z-index: 2000;
      max-height: 200px;
      overflow-y: auto;
      padding: 8px;
    }
    .suggestion-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 14px;
      border-radius: 8px;
      cursor: pointer;
      transition: 0.2s;
    }
    .suggestion-item:hover {
      background: rgba(99, 102, 241, 0.15);
    }
    .suggestion-icon {
      font-size: 1.2rem;
      width: 24px;
      display: flex;
      justify-content: center;
      color: #818cf8;
    }
    .suggestion-info {
      display: flex;
      flex-direction: column;
    }
    .suggestion-label {
      font-size: 0.9rem;
      font-weight: 500;
      color: #f1f5f9;
    }
    .suggestion-sublabel {
      font-size: 0.75rem;
      color: #94a3b8;
    }
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
    const selected = options.filter((_, i) => this.attendeeSelections[i].selected);
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
