import { Component, EventEmitter, Output, inject, OnInit, ViewChild, ElementRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api';
import { Subject, debounceTime, distinctUntilChanged, switchMap, of } from 'rxjs';

interface Attendee {
  id: string;
  name: string;
  email: string;
  department: string;
  importance: 'required' | 'optional';
}

@Component({
  selector: 'app-schedule-form',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="form-card">
      <div class="form-header">
        <h2>Schedule a Meeting</h2>
        <p>Fill in the details to start the planning conversation.</p>
      </div>

      <div class="form-body">
        <!-- Topic -->
        <div class="field" [class.invalid]="submitted && !subject">
          <label>Meeting Topic *</label>
          <input type="text" [(ngModel)]="subject" placeholder="e.g. Sprint Review, Architecture Deep Dive">
          <p class="error-text" *ngIf="submitted && !subject.trim()">Subject is required.</p>
        </div>

        <!-- Team Context & Attendee Search Row -->
        <div class="row">
          <div class="field" [class.invalid]="submitted && !selectedTeam">
            <label>Team Context *</label>
            <div class="search-wrapper">
              <input type="text" [(ngModel)]="teamSearchQuery" (input)="filterTeams()" (focus)="showTeamDropdown = true; filterTeams()" (blur)="hideTeamDropdown()" placeholder="Search teams...">
              <div class="search-results" *ngIf="showTeamDropdown && filteredTeams.length > 0" (mousedown)="$event.preventDefault()">
                <div *ngFor="let t of filteredTeams" class="result-item" (click)="selectTeam(t)">{{t}}</div>
              </div>
            </div>
          </div>
          <div class="field">
            <label>Add Attendee</label>
            <div class="search-wrapper">
              <input type="text" #attendeeInput
                     (input)="onSearchInput($event)" 
                     (focus)="showAttendeeDropdown = true; onSearchInput({target:{value:''}})"
                     (blur)="showAttendeeDropdown = false"
                     placeholder="Name or EID..." 
                     autocomplete="off">
              <div class="search-results" *ngIf="showAttendeeDropdown && searchResults.length > 0" (mousedown)="$event.preventDefault()">
                <div *ngFor="let res of searchResults" class="result-item" (click)="addAttendee(res)">
                  <div class="res-name">{{res.name}} <small>(ID: {{res.id}})</small></div>
                  <div class="res-meta">{{res.department}} • {{res.email}}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Selected Attendees Area -->
        <div class="attendee-list" *ngIf="selectedAttendees.length > 0">
          <div *ngFor="let a of selectedAttendees" class="attendee-chip">
            <div class="chip-info">
              <span class="chip-name">{{a.name}} <small>(EID: {{a.id}})</small></span>
              <span class="chip-email">{{a.email}}</span>
            </div>
            <div class="chip-actions">
              <select [(ngModel)]="a.importance" class="mini-select" (change)="$event.stopPropagation()">
                <option value="required">Req</option>
                <option value="optional">Opt</option>
              </select>
              <button class="remove-chip" (click)="removeAttendee(a)">×</button>
            </div>
          </div>
        </div>

        <!-- Date & Time Row -->
        <div class="row">
          <div class="field" [class.invalid]="submitted && !date">
            <label>Date *</label>
            <input type="date" [(ngModel)]="date" [min]="todayStr" (focus)="showDatePicker($event)">
          </div>
          <div class="field" [class.invalid]="submitted && !time12">
            <label>Time ({{userTimeZone}}) *</label>
            <div style="display: flex; gap: 8px;">
              <input type="text" [(ngModel)]="time12" placeholder="10:00" style="flex: 1;">
              <select [(ngModel)]="amPm" style="width: 80px;">
                <option value="AM">AM</option>
                <option value="PM">PM</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Duration & Recurrence Row (CHIPS) -->
        <div class="row">
          <div class="field">
            <label>Duration</label>
            <div class="chip-selection">
              <button class="select-chip" [class.active]="duration === '30'" (click)="duration = '30'">30m</button>
              <button class="select-chip" [class.active]="duration === '60'" (click)="duration = '60'">1h</button>
              <button class="select-chip" [class.active]="duration === '90'" (click)="duration = '90'">1.5h</button>
              <button class="select-chip" [class.active]="duration === '120'" (click)="duration = '120'">2h</button>
            </div>
          </div>
          <div class="field">
            <label>Recurrence</label>
            <div class="chip-selection">
              <button class="select-chip" [class.active]="recurrence === 'once'" (click)="recurrence = 'once'">Once</button>
              <button class="select-chip" [class.active]="recurrence === 'daily'" (click)="recurrence = 'daily'">Daily</button>
              <button class="select-chip" [class.active]="recurrence === 'weekly'" (click)="recurrence = 'weekly'">Weekly</button>
              <button class="select-chip" [class.active]="recurrence === 'monthly'" (click)="recurrence = 'monthly'">Monthly</button>
            </div>
          </div>
        </div>

        <!-- Room & Location Row -->
        <div class="row">
          <div class="field">
            <label>Meeting Room</label>
            <div class="search-wrapper">
              <input type="text" [(ngModel)]="room" (input)="onRoomInput(room)" (focus)="showRoomDropdown = true; onRoomInput(room)" (blur)="showRoomDropdown = false" placeholder="Find a room...">
              <div class="search-results" *ngIf="showRoomDropdown && roomSuggestions.length > 0" (mousedown)="$event.preventDefault()">
                <div *ngFor="let r of roomSuggestions" class="result-item" (click)="selectRoom(r)">{{r}}</div>
              </div>
            </div>
          </div>
          <div class="field">
            <label>External Link / Location</label>
            <div class="search-wrapper">
              <input type="text" [(ngModel)]="locationStr" (input)="onLocationInput(locationStr)" (focus)="showLocationDropdown = true; onLocationInput(locationStr)" (blur)="showLocationDropdown = false" placeholder="Teams, Zoom, etc.">
              <div class="search-results" *ngIf="showLocationDropdown && locationSuggestions.length > 0" (mousedown)="$event.preventDefault()">
                <div *ngFor="let l of locationSuggestions" class="result-item" (click)="selectLocation(l)">{{l}}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Presenter -->
        <div class="field search-field">
          <label>Meeting Presenter</label>
          <div class="search-wrapper">
            <input type="text" 
                   [(ngModel)]="presenterSearchQuery"
                   (input)="onPresenterSearchInput($event)" 
                   (focus)="showPresenterDropdown = true; onPresenterSearchInput({target:{value:''}})"
                   (blur)="showPresenterDropdown = false"
                   placeholder="Search or pick organiser..." 
                   autocomplete="off">
            <div class="search-results" *ngIf="showPresenterDropdown && presenterSearchResults.length > 0" (mousedown)="$event.preventDefault()">
              <div *ngFor="let res of presenterSearchResults" class="result-item" (click)="selectPresenter(res)">
                <div class="res-name">{{res.name}} <small>(EID: {{res.id}})</small></div>
                <div class="res-meta">{{res.department}} • {{res.email}}</div>
              </div>
            </div>
          </div>
          <div class="attendee-chip" *ngIf="presenter" style="margin-top: 10px; width: fit-content; background: #334155;">
            <div class="chip-info">
              <span class="chip-name">{{presenter}}</span>
              <span class="chip-email">Selected Presenter</span>
            </div>
            <button class="remove-chip" (click)="removePresenter()">×</button>
          </div>
        </div>
      </div>

      <div class="form-footer">
        <p class="error-msg" *ngIf="submitted && !isFormValid()">Please fill all required fields (*) before proceeding.</p>
        <button class="submit-btn" (click)="submit()">
          🚀 Hand Over to AI Assistant
        </button>
      </div>
    </div>
  `,
  styles: [`
    .form-card {
      background: rgba(30, 41, 59, 0.4);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 20px;
      padding: 28px;
      display: flex;
      flex-direction: column;
      gap: 20px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
      animation: slideUp 0.4s ease-out;
    }

    @keyframes slideUp {
      from { opacity: 0; transform: translateY(20px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .form-header h2 { font-size: 1.8rem; font-weight: 700; margin: 0 0 6px 0; color: white; }
    .form-header p { font-size: 0.95rem; color: #94a3b8; margin: 0; }

    .form-body { display: flex; flex-direction: column; gap: 14px; }

    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .field { display: flex; flex-direction: column; gap: 4px; position: relative; }
    .field.invalid label { color: #ef4444; }
    .field.invalid input, .field.invalid .search-wrapper input { border-color: #ef4444; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.1); }
    .error-text { color: #ef4444; font-size: 0.72rem; margin-top: 4px; font-weight: 500; animation: fadeIn 0.3s ease; }
    .eid-tag { color: #94a3b8; font-size: 0.75rem; font-weight: 400; margin-left: 6px; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: translateY(0); } }
    label { font-size: 0.82rem; font-weight: 600; color: #6366f1; text-transform: uppercase; letter-spacing: 0.05em; }

    input, select {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 14px 16px;
      color: white;
      font-size: 1rem;
      outline: none;
      transition: all 0.2s;
    }

    input:focus, select:focus { border-color: #818cf8; background: rgba(15, 23, 42, 0.8); }

    .search-wrapper { position: relative; }
    .search-results {
      position: absolute;
      top: 100%; left: 0; right: 0;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      margin-top: 8px;
      max-height: 250px;
      overflow-y: auto;
      z-index: 200;
      box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
    }

    .result-item { padding: 12px 16px; cursor: pointer; border-bottom: 1px solid #334155; }
    .result-item:hover { background: #334155; }
    .res-name { font-weight: 600; color: #f1f5f9; }
    .res-meta { font-size: 0.75rem; color: #94a3b8; }

    .chip-selection {
      display: flex;
      gap: 6px;
      padding: 6px;
      background: rgba(15, 23, 42, 0.4);
      border-radius: 12px;
      border: 1px solid #334155;
      width: 100%;
    }

    .select-chip {
      flex: 1;
      background: transparent;
      border: none;
      color: #94a3b8;
      padding: 12px 6px;
      font-size: 0.85rem;
      font-weight: 700;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      white-space: nowrap;
    }

    .select-chip.active {
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: white;
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
    }

    .select-chip:hover:not(.active) {
      background: rgba(255, 255, 255, 0.05);
      color: #f1f5f9;
    }

    .attendee-list { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
    .attendee-chip {
      background: #334155;
      padding: 8px 12px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      gap: 15px;
      border: 1px solid #475569;
    }

    .chip-info { display: flex; flex-direction: column; }
    .chip-name { font-size: 0.88rem; font-weight: 600; color: white; }
    .chip-email { font-size: 0.7rem; color: #94a3b8; }
    .chip-actions { display: flex; align-items: center; gap: 8px; }

    .mini-select { padding: 4px 8px; font-size: 0.75rem; border-radius: 6px; }
    .remove-chip { background: transparent; color: #ef4444; border: none; font-size: 1.2rem; cursor: pointer; padding: 0 5px; }

    .submit-btn {
      width: 100%;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: white;
      border: none;
      padding: 18px;
      border-radius: 16px;
      font-size: 1.1rem;
      font-weight: 700;
      cursor: pointer;
      transition: 0.2s;
      margin-top: 10px;
    }

    .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .submit-btn:hover:not(:disabled) { transform: translateY(-2px); filter: brightness(1.1); box-shadow: 0 10px 20px rgba(99, 102, 241, 0.4); }

    .error-msg { color: #ef4444; font-size: 0.85rem; text-align: center; margin-bottom: 10px; font-weight: 500; }

    .clear-inp-btn {
      position: absolute; right: 10px; top: 12px;
      background: none; border: none; color: #94a3b8; font-size: 1.2rem; cursor: pointer;
    }
    .clear-inp-btn:hover { color: #ef4444; }
  `]
})
export class ScheduleFormComponent implements OnInit {
  @ViewChild('attendeeInput') attendeeInput!: ElementRef;
  api = inject(ApiService);
  @Output() submitForm = new EventEmitter<any>();

  subject = '';
  selectedTeam = 'General';
  teams: string[] = [];
  searchResults: any[] = [];
  selectedAttendees: Attendee[] = [];
  date = '';
  time12 = '';
  amPm = 'AM';
  duration = '60';
  room = '';
  locationStr = '';
  presenter = '';
  recurrence = 'once';
  todayStr = new Date().toISOString().split('T')[0];
  userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  submitted = false;

  teamSearchQuery = '';
  showTeamDropdown = false;
  filteredTeams: string[] = [];

  presenterSearchQuery = '';
  presenterSearchResults: any[] = [];
  private presenterSearchSubject = new Subject<string>();

  private searchSubject = new Subject<string>();

  showRoomDropdown = false;
  showLocationDropdown = false;
  showAttendeeDropdown = false;
  showPresenterDropdown = false;

  roomSuggestions: string[] = [];
  locationSuggestions: string[] = [];
  private roomSearchSubject = new Subject<string>();
  private locationSearchSubject = new Subject<string>();

  ngOnInit() {
    this.api.getTeams().subscribe(t => {
      this.teams = t;
      this.filteredTeams = t;
    });

    this.searchSubject.pipe(
      debounceTime(300),
      switchMap(term => (term.length >= 0 || this.selectedTeam) ? this.api.searchUsers(term, [this.selectedTeam]) : of([]))
    ).subscribe(results => {
      this.searchResults = results;
    });

    this.presenterSearchSubject.pipe(
      debounceTime(300),
      switchMap(term => {
        const teams = this.selectedTeam === 'General' ? [] : [this.selectedTeam];
        return this.api.searchUsers(term, teams);
      })
    ).subscribe(res => {
      const organiser = { id: '101', name: 'Poojitha Reddy', email: 'poojitha.reddy@example.com', department: 'Engineering', jobTitle: 'Engineering Manager' };
      let final = res;
      if (!res.find(u => u.id === '101')) {
        final = [organiser, ...res];
      }
      this.presenterSearchResults = final;
    });

    this.roomSearchSubject.pipe(
      debounceTime(300),
      switchMap(term => {
        const { start, end } = this.getTimeRange();
        return this.api.getRoomSuggestions(term, start, end);
      })
    ).subscribe(res => this.roomSuggestions = res);

    this.locationSearchSubject.pipe(
      debounceTime(300),
      switchMap(term => this.api.getLocationSuggestions(term))
    ).subscribe(res => this.locationSuggestions = res);
  }

  filterTeams() {
    const q = this.teamSearchQuery.toLowerCase();
    this.filteredTeams = this.teams.filter(t => t.toLowerCase().includes(q));
  }

  selectTeam(t: string) {
    this.selectedTeam = t;
    this.teamSearchQuery = t;
    this.showTeamDropdown = false;
    this.onTeamChange();

    setTimeout(() => {
      if (this.attendeeInput) {
        this.attendeeInput.nativeElement.focus();
        this.showAttendeeDropdown = true;
      }
    }, 100);
  }

  clearTeams() {
    this.selectedTeam = 'General';
    this.teamSearchQuery = '';
    this.onTeamChange();
  }

  hideTeamDropdown() {
    setTimeout(() => this.showTeamDropdown = false, 150);
  }

  onSearchInput(event: any) {
    this.searchSubject.next(event.target.value);
  }

  onPresenterSearchInput(evt: any) {
    this.presenterSearchSubject.next(evt.target.value);
  }

  onTeamChange() {
    this.searchResults = [];
    this.searchSubject.next('');
  }

  addAttendee(user: any) {
    if (!this.selectedAttendees.find(a => a.id === user.id)) {
      this.selectedAttendees.push({
        id: user.id,
        name: user.name,
        email: user.email,
        department: user.department || 'N/A',
        importance: 'required'
      });
    }
    this.searchResults = [];
  }

  removeAttendee(a: Attendee) {
    this.selectedAttendees = this.selectedAttendees.filter(item => item.id !== a.id);
  }

  selectPresenter(user: any) {
    this.presenter = user.name;
    this.presenterSearchQuery = '';
    this.presenterSearchResults = [];
  }

  removePresenter() {
    this.presenter = '';
    this.presenterSearchQuery = '';
  }

  onRoomInput(val: string) { this.roomSearchSubject.next(val); }
  onLocationInput(val: string) { this.locationSearchSubject.next(val); }

  selectRoom(r: string) { this.room = r; this.showRoomDropdown = false; }
  selectLocation(l: string) { this.locationStr = l; this.showLocationDropdown = false; }

  getTimeRange() {
    if (!this.date || !this.time12) return { start: '', end: '' };
    try {
      let [h, m] = this.time12.split(':').map(Number);
      if (isNaN(m)) m = 0;
      if (this.amPm === 'PM' && h < 12) h += 12;
      if (this.amPm === 'AM' && h === 12) h = 0;

      const start = new Date(`${this.date}T${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:00`);
      const end = new Date(start.getTime() + Number(this.duration) * 60000);
      return { start: start.toISOString(), end: end.toISOString() };
    } catch {
      return { start: '', end: '' };
    }
  }

  showDatePicker(event: any) {
    if ('showPicker' in event.target) {
      try {
        event.target.showPicker();
      } catch (e) {
        console.warn('showPicker failed:', e);
      }
    }
  }

  isFormValid() {
    const isSubjectValid = !!this.subject.trim();
    const isTeamValid = !!this.selectedTeam;
    const isAttendeesValid = this.selectedAttendees.length > 0;
    const isDateValid = !!this.date && this.date >= this.todayStr;
    const isTimeValid = !!this.time12;

    return isSubjectValid && isTeamValid && isAttendeesValid && isDateValid && isTimeValid;
  }

  submit() {
    this.submitted = true;
    if (!this.isFormValid()) return;

    this.submitForm.emit({
      subject: this.subject,
      team: this.selectedTeam,
      attendees: this.selectedAttendees,
      date: this.date,
      time: `${this.time12} ${this.amPm}`,
      timezone: this.userTimeZone,
      duration: this.duration,
      recurrence: this.recurrence,
      room: this.room,
      location: this.locationStr,
      presenter: this.presenter
    });
  }
}
