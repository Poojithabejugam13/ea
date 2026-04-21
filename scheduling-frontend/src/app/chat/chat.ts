import { Component, ChangeDetectorRef, ViewChild, ElementRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api';
import { ScheduleFormComponent } from '../schedule-form/schedule-form';
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
  finalSelections?: Record<string, string>;
}

interface DisambigPerson {
  label: string;   // full raw option string from backend
  name: string;
  dept: string;
  eid: string;
  email?: string;
  selected: boolean;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule, ScheduleFormComponent],
  template: `
    <div class="chat-wrapper">
      <div class="chat-container">
        
        <div class="messages" #scrollContainer>
          <div *ngFor="let msg of messages; let i = index" class="message-row" [class.user-row]="msg.role === 'user'">
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

              <!-- Interactive Sections -->
              <div *ngIf="msg.isInteractive" class="interactive-area" [class.past-card]="i !== messages.length - 1">
                
                <!-- GATHERING CARD: 1:1 slot_selection and group_selection -->
                <div *ngIf="msg.optionType === 'gathering_card'" class="card-wrapper">
                  <ng-container *ngFor="let section of objectKeys(msg.titledSections || {})">
                    <div class="section-container">
                      <p class="section-header">{{ section }}</p>

                      <!-- DROPDOWN: Single-select topics/presenters -->
                      <ng-container *ngIf="(section.toLowerCase().includes('presenter') || section.toLowerCase().includes('participant') || section.toLowerCase().includes('topic')) && !section.toLowerCase().includes('(multi)')">
                        <select
                          class="copilot-select"
                          (change)="onDropdownChange(section, $event)"
                          [value]="msg.finalSelections?.[section] || cardSelections[section] || ''"
                          [disabled]="i !== messages.length - 1"
                        >
                          <option value="" disabled selected>Select {{ section.toLowerCase().includes('topic') ? 'Topic' : 'Presenter' }}</option>
                          <option *ngFor="let opt of filterOpts(msg.titledSections[section])" [value]="cleanOpt(opt)">
                            {{ cleanOpt(opt) }}
                          </option>
                        </select>
                      </ng-container>

                      <!-- MULTI-SELECT or RADIO: Buttons -->
                      <ng-container *ngIf="(!section.toLowerCase().includes('presenter') && !section.toLowerCase().includes('participant') && !section.toLowerCase().includes('topic') && !isTextSection(section)) || section.toLowerCase().includes('(multi)')">
                        <div class="stacked-buttons">
                          <button 
                            *ngFor="let opt of filterOpts(msg.titledSections[section])" 
                            class="choice-btn"
                            [class.selected]="isChipSelected(section, opt) || msg.finalSelections?.[section]?.includes(cleanOpt(opt)) || cardSelections[section]?.includes(cleanOpt(opt)) || opt.startsWith('✅')"
                            [disabled]="i !== messages.length - 1 || loading || !!msg.finalSelections"
                            (click)="section.toLowerCase().includes('(multi)') ? onChipToggle(section, opt, msg) : onCardTap(section, opt, msg)"
                          >
                            <div class="radio-ring" *ngIf="!section.toLowerCase().includes('(multi)')">
                              <div class="radio-dot" *ngIf="cardSelections[section] ? cardSelections[section] === cleanOpt(opt) : opt.startsWith('✅')"></div>
                            </div>
                            <div class="checkbox" *ngIf="section.toLowerCase().includes('(multi)')" [class.checked]="isChipSelected(section, opt) || msg.finalSelections?.[section]?.includes(cleanOpt(opt)) || cardSelections[section]?.includes(cleanOpt(opt)) || opt.startsWith('✅')">
                              <span *ngIf="isChipSelected(section, opt) || msg.finalSelections?.[section]?.includes(cleanOpt(opt)) || cardSelections[section]?.includes(cleanOpt(opt)) || opt.startsWith('✅')">✓</span>
                            </div>
                            <span class="btn-label">{{ cleanOpt(opt) }}</span>
                          </button>
                        </div>
                        
                        <!-- Manual Topic Input if 'Other' is selected -->
                        <div *ngIf="section.toLowerCase().includes('topic') && (msg.finalSelections?.[section] === 'Other' || cardSelections[section] === 'Other' || hasOtherSelected(msg.titledSections[section]))" class="text-input-area mt-2" style="margin-top: 10px;">
                          <input 
                            type="text" 
                            class="card-text-input" 
                            placeholder="Type topic here..." 
                            [value]="msg.finalSelections?.['topicOtherText'] || topicOtherText"
                            (input)="onTopicOtherInput($event)"
                            [disabled]="i !== messages.length - 1 || loading || !!msg.finalSelections"
                          >
                        </div>
                      </ng-container>

                      <!-- TEXT INPUT section -->
                      <ng-container *ngIf="isTextSection(section)">
                        <div class="text-input-area">
                          <input 
                            type="text" 
                            class="card-text-input" 
                            placeholder="Type here..." 
                            [value]="msg.finalSelections?.[section] || cardSelections[section] || ''"
                            (input)="onTextInput(section, $event)"
                            [disabled]="i !== messages.length - 1 || loading || !!msg.finalSelections"
                          >
                        </div>
                      </ng-container>

                    </div>
                  </ng-container>

                  <!-- Confirm & Book (disabled until all sections have a selection) -->
                  <div class="confirm-row" *ngIf="i === messages.length - 1">
                    <button 
                      class="confirm-btn" 
                      [disabled]="!isGatheringComplete(msg) || loading || !!msg.finalSelections"
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
                      [disabled]="i !== messages.length - 1 || loading"
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
                    [disabled]="i !== messages.length - 1 || loading"
                    (click)="handleEditTapped(opt, msg.meetingData)"
                  >
                    {{ opt }}
                  </button>
                </div>

                <!-- DISAMBIGUATION CARD — rich person picker -->
                <div *ngIf="msg.intent === 'attendee_disambiguation' || msg.optionType === 'attendee_disambiguation'" class="disambig-card">
                  <div class="disambig-header">
                    <span class="disambig-title">👥 Select Attendee</span>
                  </div>
                  <div class="disambig-list">
                    <div
                      *ngFor="let p of getDisambigPeople(msg.options || [])"
                      class="person-row"
                      [class.selected]="isPersonSelected(p)"
                      (click)="onPersonTap(p, msg, i)"
                    >
                      <div class="person-details">
                        <span class="person-name">{{ p.name }}</span>
                        <div class="person-meta">
                          <span>{{ p.dept }} • EID: {{ p.eid }}</span>
                          <span *ngIf="p.email" class="person-email">📧 {{ p.email }}</span>
                        </div>
                      </div>
                      <div class="checkbox" [class.checked]="isPersonSelected(p)">
                        <span *ngIf="isPersonSelected(p)">✓</span>
                      </div>
                    </div>
                  </div>
                  <button
                    class="disambig-confirm-btn"
                    [disabled]="selectedDisambigPeople.size === 0 || loading || i !== messages.length - 1"
                    (click)="confirmDisambig(msg)"
                  >
                    Confirm Selection ({{selectedDisambigPeople.size}})
                  </button>
                </div>

                <!-- GENERAL (stacked fallback for any other option type) -->
                <div *ngIf="msg.intent !== 'attendee_disambiguation' && msg.optionType !== 'attendee_disambiguation' && !['gathering_card', 'edit_grid', 'conflict'].includes(msg.optionType || '') && (msg.options?.length || 0) > 0" class="stacked-buttons" style="margin-top: 12px;">
                  <button 
                    *ngFor="let opt of msg.options" 
                    class="choice-btn"
                    [disabled]="i !== messages.length - 1 || loading"
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
          <div class="input-wrapper">
            <input 
              type="text" 
              [(ngModel)]="prompt" 
              (keyup.enter)="sendMessage()" 
              placeholder="Message ARIA..."
            >
            <button class="send-btn" (click)="sendMessage()" [disabled]="loading">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            </button>
          </div>
        </div>

        <div *ngIf="showMeetingEditor" class="editor-overlay">
          <div class="popup-wrapper" [style.transform]="'translate(' + dragX + 'px, ' + dragY + 'px)'">
            <div class="drag-handle" 
                (mousedown)="onDragStart($event)" 
                (document:mousemove)="onDrag($event)" 
                (document:mouseup)="onDragEnd()">
               <span>≡ Drag to move</span>
               <button class="close-popup-btn" (click)="closeMeetingEditor()">×</button>
            </div>
            <div class="scrollable-form">
              <app-schedule-form 
                [prefillData]="meetingEdit" 
                [updateMode]="true"
                (submitForm)="onScheduleFormSubmit($event)">
              </app-schedule-form>
            </div>
          </div>
        </div>

      </div>
    </div>
  `,
  styles: [`
    .chat-wrapper { flex: 1; display: flex; justify-content: center; background: #f5f5f5; color: #242424; height: 100%; overflow: hidden; font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif; }

    .chat-container { width: 100%; max-width: 800px; display: flex; flex-direction: column; height: 100%; }
    .messages { flex: 1; overflow-y: auto; padding: 30px 20px; display: flex; flex-direction: column; gap: 20px; scroll-behavior: smooth;}
    .message-row { display: flex; gap: 12px; width: 100%; }
    .user-row { justify-content: flex-end; }
    .msg { padding: 12px 16px; max-width: 95%; font-size: 0.95rem; line-height: 1.5; white-space: pre-wrap; position: relative; }
    .user-msg { background: #e8ebfa; border: none; color: #242424; border-radius: 8px 8px 0px 8px; }

    .assistant-msg { background: #ffffff; color: #242424; border: 1px solid #e0e0e0; border-radius: 8px 8px 8px 0px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .no-box { background: transparent !important; border: none !important; box-shadow: none !important; padding: 12px 0 !important; }

    ::ng-deep .assistant-msg h3 { margin-top: 0; }
    ::ng-deep .assistant-msg p { margin: 0 0 10px 0; }
    ::ng-deep .assistant-msg strong { font-weight: 600; }

    /* ── Dropdown ── */
    .copilot-select {
      width: 100%; padding: 12px 16px; border-radius: 12px; border: 1px solid #e2e8f0;
      background: #f8fafc; color: #1e293b; font-size: 0.95rem; outline: none; margin-top: 10px;
      appearance: none; cursor: pointer; transition: all 0.2s;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2364748b'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'%3E%3C/path%3E%3C/svg%3E");
      background-repeat: no-repeat; background-position: right 12px center; background-size: 16px;
    }
    .copilot-select:focus { border-color: #5b5fc7; background-color: #ffffff; box-shadow: 0 0 0 3px rgba(91, 95, 199, 0.1); }

    
    .join-box { background: #ffffff; border: 1px solid #e0e0e0; border-radius: 4px; padding: 14px 20px; margin: 10px 0 6px 0; width: fit-content; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

    .join-box a { color: #5b5fc7; font-weight: 600; text-decoration: none; }
    .join-box a:hover { text-decoration: underline; }

    .input-area { padding: 20px; background: transparent; display: flex; gap: 12px; align-items: center; justify-content: center; }
    .input-wrapper {
      display: flex; gap: 8px; width: 100%; background: #ffffff; padding: 6px; border-radius: 4px;
      border: 1px solid #d1d1d1; align-items: center; box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }

    input, .card-text-input { flex: 1; padding: 10px 16px; border-radius: 16px; border: none; background: transparent; color: #171717; font-size: 1rem; outline: none; }

    .card-text-input { 
      background: #ffffff; border: 1px solid #e2e8f0; width: 100%; box-sizing: border-box; 
      margin-top: 10px; border-radius: 12px; padding: 12px 16px; font-size: 0.95rem; outline: none;
      transition: all 0.2s;
    }
    .card-text-input:focus { border-color: #5b5fc7; box-shadow: 0 0 0 3px rgba(91, 95, 199, 0.1); }
    
    .send-btn { 
      background: transparent; 
      color: #5b5fc7; 
      border: none; 
      width: 36px; height: 36px; 
      border-radius: 4px; 
      display: flex; align-items: center; justify-content: center; 
      cursor: pointer; transition: 0.2s; 
    }
    .send-btn:hover:not(:disabled) { background: #f0f0f0; }
    .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    
    .thinking-wrapper { display: flex; align-items: center; gap: 12px; margin-top: 15px; padding-left: 10px; }
    .spinner-rotate { width: 20px; height: 20px; color: #5b5fc7; animation: spin 1s linear infinite; }
    .thinking-text { font-size: 0.9rem; color: #6b7280; font-weight: 500; }

    .dots span { animation: pulse 1.4s infinite; opacity: 0; }
    .dots span:nth-child(2) { animation-delay: 0.2s; }
    .dots span:nth-child(3) { animation-delay: 0.4s; }

    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    @keyframes pulse { 0% { opacity: 0; } 50% { opacity: 1; } 100% { opacity: 0; } }

    .edit-btn { 
      background: #ffffff; 
      border: 1px solid #d1d1d1; 
      color: #616161; 
      cursor: pointer; 
      padding: 4px 10px; 
      border-radius: 4px;
      font-size: 0.75rem; 
      font-weight: 500;
      transition: all 0.2s; 
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 8px;
    }
    .edit-btn:hover { background: #f5f5f5; border-color: #bdbdbd; color: #242424; }

    .edit-area { display: flex; flex-direction: column; gap: 8px; width: 100%; min-width: 300px; }
    .edit-area textarea { background: #ffffff; border: 1px solid #d1d1d1; color: #242424; padding: 10px; border-radius: 4px; font-family: inherit; resize: vertical; box-shadow: inset 0 1px 2px rgba(0,0,0,0.05); }
    .edit-actions { display: flex; gap: 8px; justify-content: flex-end; }
    .save-btn { background: #5b5fc7; color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 0.85rem; font-weight: 600; }
    .save-btn:hover { background: #4a4d9e; }
    .cancel-btn { background: #ffffff; border: 1px solid #d1d1d1; color: #616161; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 0.85rem; font-weight: 600; }
    .cancel-btn:hover { background: #f5f5f5; }

    .regenerate-btn { 
      background: #ffffff; 
      border: 1px solid #d1d1d1; 
      color: #616161; 
      cursor: pointer; 
      padding: 4px 10px; 
      border-radius: 4px;
      font-size: 0.75rem; 
      font-weight: 500;
      transition: all 0.2s; 
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 10px;
    }
    .regenerate-btn:hover { background: #f5f5f5; border-color: #bdbdbd; color: #242424; }

    .interactive-area { margin-top: 15px; width: 100%; padding: 0; background: transparent; border: none; box-shadow: none; }
    .section-header { font-weight: 600; margin-bottom: 8px; color: #171717; }
    
    .choice-btn { 
      background: transparent; 
      color: #171717; 
      border: 1px solid #e5e7eb; 
      border-radius: 8px; 
      padding: 10px 14px; 
      font-size: 0.95rem; 
      cursor: pointer; 
      text-align: left; 
      transition: all 0.2s;
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }

    .choice-btn:hover { background: #f9fafb; }

    .choice-btn.selected { border-color: #5b5fc7; background: #f3f2f1; font-weight: 600; }
    .choice-btn:disabled { opacity: 0.5; cursor: default; }
    
    .radio-ring {
      width: 18px; height: 18px; border-radius: 50%; border: 2px solid #d1d5db;
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
    }

    .choice-btn.selected .radio-ring { border-color: #5b5fc7; }
    .radio-dot { width: 10px; height: 10px; background: #5b5fc7; border-radius: 50%; }
    
    .confirm-btn { background: #5b5fc7; color: white; border: none; border-radius: 4px; padding: 10px 20px; font-weight: 600; cursor: pointer; width: 100%; margin-top: 16px; transition: 0.2s; }
    .confirm-btn:hover:not(:disabled) { background: #4a4d9e; }
    .confirm-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    .dup-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
    .dup-update { background: linear-gradient(135deg, #0f4c75, #1b6ca8); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-del { background: linear-gradient(135deg, #7f1d1d, #b91c1c); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    .dup-new { background: linear-gradient(135deg, #14532d, #15803d); color: white; border: none; border-radius: 8px; padding: 12px; font-weight: 600; cursor: pointer; }
    
    .attendee-row { display: flex; align-items: center; justify-content: space-between; background: #f3f2f1; padding: 10px 15px; border-radius: 8px; margin-bottom: 8px; }
    .attendee-row label { display: flex; align-items: center; gap: 10px; cursor: pointer; color: #242424; }
    .attendee-row select { background: #ffffff; border: 1px solid #d1d1d1; color: #242424; padding: 6px; border-radius: 6px; outline: none; }
    .editor-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; backdrop-filter: blur(4px); }
    .popup-wrapper { width: min(800px, 95vw); max-height: 90vh; display: flex; flex-direction: column; background: #ffffff; border-radius: 8px; box-shadow: 0 20px 40px rgba(0,0,0,0.2); }
    .drag-handle { background: #f3f2f1; color: #424242; padding: 12px 24px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center; cursor: move; border-bottom: 1px solid #e0e0e0; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
    .scrollable-form { overflow-y: auto; overflow-x: hidden; border-radius: 0 0 8px 8px; background: #ffffff; }
    .scrollable-form::-webkit-scrollbar { width: 8px; }
    .scrollable-form::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.6); }
    .scrollable-form::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
    .scrollable-form::-webkit-scrollbar-thumb:hover { background: #64748b; }

    /* ── Disambiguation Card ── */
    .disambig-card { margin-top: 14px; padding: 0; }
    .disambig-header { margin-bottom: 16px; padding-bottom: 4px; }
    .disambig-title { font-size: 0.85rem; font-weight: 700; color: #5b5fc7; text-transform: uppercase; letter-spacing: 0.05em; }
    
    .disambig-list { display: flex; flex-direction: column; }
    .person-row { 
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 8px; border-radius: 8px; cursor: pointer; transition: 0.2s;
      margin-bottom: 2px;
    }
    .person-row:hover { background: #f8f8f8; }
    .person-row.selected { background: transparent; }
    
    .person-details { display: flex; flex-direction: column; gap: 2px; }
    .person-name { font-size: 0.95rem; font-weight: 600; color: #242424; }
    .person-meta { display: flex; flex-direction: column; font-size: 0.75rem; color: #6b7280; }
    .person-email { color: #5b5fc7; margin-top: 1px; }
    
    .checkbox { 
      width: 20px; height: 20px; border: 2px solid #5b5fc7; border-radius: 4px;
      display: flex; align-items: center; justify-content: center;
      transition: 0.2s; color: white; font-size: 0.8rem; font-weight: bold;
    }
    .checkbox.checked { background: #5b5fc7; }

    .disambig-confirm-btn { 
      width: 100%; background: #5b5fc7; color: white; border: none; padding: 12px; 
      border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; 
      margin-top: 16px; 
    }
    .disambig-confirm-btn:disabled { opacity: 0.4; cursor: not-allowed; }
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
      content: '👋 Hi! I am ARIA, your AI Scheduling Assistant.\n\nHow can I help you schedule today?'
    }
  ];

  // For attendee selection state
  attendeeSelections: { selected: boolean, importance: string }[] = [];
  confirmCandidateChoice = '';
  showMeetingEditor = false;
  meetingEdit: any = {};

  // ── Disambiguation state ─────────────────────────────────────────────────
  disambigBulkMode = true;           // Enabled multi-select by default
  selectedDisambigPeople = new Set<string>(); // selected EIDs
  private disambigCache = new Map<string, DisambigPerson>(); // label → parsed person

  dragX = 0;
  dragY = 0;
  isDragging = false;
  startX = 0;
  startY = 0;

  cardSelections: Record<string, string> = {};

  constructor() { }

  formatMessage(text: string): string {
    if (!text) return '';
    // Bold
    let html = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Code inline
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Preserve box drawing / monospace blocks
    const lines = html.split('\n');
    const processed = lines.map(line => {
      if (/^[│┌┐└┘╔╠╚╗╝║━─┼├┤┬┴╣╦╩╬\s]/.test(line) && line.trim().length > 0) {
        return `<span class="box-line">${line}</span>`;
      }
      return line;
    });
    return processed.join('<br>');
  }

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

  topicOtherText = '';

  onTopicOtherInput(event: Event) {
    this.topicOtherText = (event.target as HTMLInputElement).value;
  }

  onDropdownChange(section: string, event: Event) {
    const val = (event.target as HTMLSelectElement).value;
    this.cardSelections[section] = val;
    this.cdr.detectChanges();
  }

  hasOtherSelected(opts: any[]): boolean {
    return opts && opts.some(o => o.startsWith('✅') && this.cleanOpt(o) === 'Other');
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

  onCardTap(sectionKey: string, opt: string, msg: Message) {
    if (this.loading || !!msg.finalSelections) return;
    const raw = this.cleanOpt(opt);
    if (!msg.titledSections) return;

    // Store in local state (never call backend here)
    this.cardSelections[sectionKey] = raw;

    // Mark selected, unmark all others in this section
    msg.titledSections[sectionKey] = (msg.titledSections[sectionKey] as string[]).map((o: string) => {
      const clean = this.cleanOpt(o);
      return clean === raw ? `✅ ${clean}` : clean;
    });

    // Clear other text if not Other
    if (sectionKey.toLowerCase().includes('topic') && raw !== 'Other') {
      this.topicOtherText = '';
    }

    this.cdr.detectChanges();
  }

  /** Handle conflict card tap — alternate slot or proceed original */
  onConflictTap(opt: string) {
    if (this.loading) return;
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
      if (section.toLowerCase().includes('topic') && value === 'Other' && this.topicOtherText.trim()) {
        parts.push(`${section}=${this.topicOtherText.trim()}`);
      } else {
        parts.push(`${section}=${value}`);
      }
    }
    const payload = parts.join(' | ');
    msg.finalSelections = { ...this.cardSelections, topicOtherText: this.topicOtherText };
    this.cardSelections = {}; // reset for next card
    this.multiSelections = {}; // reset multi-select too
    this.topicOtherText = '';

    // Send the machine payload to backend directly
    this.processQuery(payload);
  }

  isGatheringComplete(msg: Message): boolean {
    if (!msg.titledSections) return true;
    for (const key in msg.titledSections) {
      if (!msg.titledSections.hasOwnProperty(key)) continue;

      if (this.isTextSection(key)) {
        if (!this.cardSelections[key] || !this.cardSelections[key].trim()) return false;
      } else if (key.toLowerCase().includes('presenter') || key.toLowerCase().includes('participant')) {
        if (!this.cardSelections[key]) return false;
      } else {
        const hasSelection = (msg.titledSections[key] || []).some((opt: string) => opt.startsWith('✅') || this.cardSelections[key] === this.cleanOpt(opt));
        if (!hasSelection) return false;

        if (key.toLowerCase().includes('topic') && (this.cardSelections[key] === 'Other' || this.hasOtherSelected(msg.titledSections[key]))) {
          if (!this.topicOtherText || !this.topicOtherText.trim()) return false;
        }
      }
    }
    return true;
  }

  handleEditTapped(option: string, meetingData: any) {
    if (this.loading) return;
    if (option.includes('Cancel')) {
      this.sendAction('Cancel meeting');
      return;
    }
    if (option.includes('Proceed')) {
      this.sendAction('Proceed with booking');
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
    if (this.loading || !this.prompt.trim()) return;
    const userText = this.prompt;
    this.prompt = '';

    this.messages.push({ role: 'user', content: userText });
    this.scrollToBottom();

    this.processQuery(userText);
  }

  sendAction(actionText: string) {
    if (this.loading) return;
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

    this.processQuery(newContent, index);
  }

  processQuery(q: string, truncateHistory?: number) {
    this.loading = true;
    this.loadingMessage = 'ARIA is thinking';
    this.scrollToBottom();

    this.api.processMessage(q, this.sessionId, truncateHistory).subscribe({
      next: (res) => {
        this.loading = false;

        // Extract teams links
        const rawResp = res?.response || '';
        const linkRegex = /https:\/\/teams\.\S+/g;
        let links = rawResp.match(linkRegex) || [];
        if (res?.links && Array.isArray(res.links)) {
          links = [...links, ...res.links];
        }

        // Parse raw JSON if returned in response text
        let finalResponse = rawResp;
        let finalOptions = res?.options || [];
        let finalOptionType = res?.option_type || 'general';
        let finalIntent = res?.intent || '';
        let finalTitledSections = res?.titled_sections || {};

        if (finalResponse.trim().startsWith('{') && finalResponse.trim().endsWith('}')) {
          try {
            const parsed = JSON.parse(finalResponse);
            if (parsed.message) finalResponse = parsed.message;
            if (parsed.options) finalOptions = parsed.options;
            if (parsed.type) finalOptionType = parsed.type;
            if (parsed.intent) finalIntent = parsed.intent;
            if (parsed.titled_sections) finalTitledSections = parsed.titled_sections;
          } catch (e) {}
        }

        const isInteractive = (finalOptions && finalOptions.length > 0) ||
          ['duplicate_action', 'title_and_agenda', 'attendee_confirm', 'timeslot', 'conflict', 'attendee', 'attendee_disambiguation', 'gathering_card'].includes(finalOptionType);

        // Reset disambiguation state for each new response
        this.selectedDisambigPeople.clear();
        this.disambigCache.clear();
        this.disambigBulkMode = true; // Default to multi-select

        this.messages.push({
          role: 'assistant',
          content: finalResponse,
          links: links,
          options: finalOptions,
          optionType: finalOptionType,
          titledSections: finalTitledSections,
          existingMeeting: res?.existing_meeting || null,
          meetingData: res?.meeting_data || null,
          candidateOptions: res?.candidate_options || [],
          selectionMap: res?.selection_map || {},
          isInteractive,
          intent: finalIntent
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
      event_id: meetingData?.event_id || meetingData?.id || '',
      fingerprint: meetingData?.fingerprint || meetingData?.new_fingerprint || '',
      subject: meetingData?.subject || '',
      agenda: meetingData?.agenda || meetingData?.bodyPreview || '',
      location: meetingData?.location || meetingData?.room || 'Virtual',
      presenter: meetingData?.presenter || '',
      recurrence: meetingData?.recurrence || 'none',
      start: start.toISOString(),
      end: end.toISOString(),
      duration: duration.toString(),
      attendees: meetingData?.attendees || [],
      attendee_ids: meetingData?.attendee_ids || meetingData?.attendeeIds || [],
      room: meetingData?.room || '',
      team: meetingData?.team || 'General'
    };
    this.showMeetingEditor = true;
  }

  closeMeetingEditor() {
    this.showMeetingEditor = false;
    this.meetingEdit = {};
    this.dragX = 0;
    this.dragY = 0;
  }

  onScheduleFormSubmit(event: any) {
    this.closeMeetingEditor();

    let attendeesText = '';
    if (event.attendees && event.attendees.length > 0) {
     attendeesText = event.selectedAttendees.map((a: any) => `${a.name} (EID: ${a.id}) (Email: ${a.email}) (${a.importance})`).join(', ');
    }

    const promptText = `[structured form submission]
Please update the meeting.
Event ID: ${event.eventId || 'N/A'}
Topic: ${event.subject}
Date: ${event.date}
Time: ${event.time}
Duration: ${event.duration} mins
Recurrence: ${event.recurrence}
Room: ${event.room || 'Not specified'}
Location: ${event.location || 'Not specified'}
Presenter: ${event.presenter || 'Not specified'}
Attendees: ${attendeesText}`;

    this.messages.push({ role: 'user', content: 'Updating meeting details via form...' });
    this.processQuery(promptText);
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
      
      // Robust extraction for email and eid even if the full regex fails
      const emailMatch = label.match(/Email:\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/i) || label.match(/Email:\s*([^\s]+)/i);
      const eidMatch = label.match(/EID:\s*(\w+)/i);
      const nameMatch = label.match(/Select:\s*(.+?)\s*\(/i);
      const deptMatch = label.match(/\((.+?)\)/i);

      const person: DisambigPerson = {
        label,
        name: nameMatch ? nameMatch[1].trim() : label.split('(')[0].replace('Select:', '').trim(),
        dept: deptMatch ? deptMatch[1].trim() : '',
        email: emailMatch ? emailMatch[1].trim() : '',
        eid: eidMatch ? eidMatch[1].trim() : '',
        selected: false,
      };
      this.disambigCache.set(label, person);
      return person;
    });
  }

  isPersonSelected(p: DisambigPerson): boolean {
    return this.selectedDisambigPeople.has(p.eid);
  }

  onPersonTap(p: DisambigPerson, msg: Message, i: number) {
    if (this.loading || i !== this.messages.length - 1) return;
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
}
