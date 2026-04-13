import { Component, ChangeDetectorRef, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatComponent } from './chat/chat';
import { ScheduleFormComponent } from './schedule-form/schedule-form';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, ChatComponent, ScheduleFormComponent],
  template: `
    <div class="app-container">
      <header class="top-nav">
        <div class="brand">
          <span class="sparkle">✨</span>
          <h1>AI Butler</h1>
        </div>
        <button class="schedule-btn" (click)="toggleForm()">
          📅 {{ showForm ? 'Back to Chat' : 'Schedule Meeting' }}
        </button>
      </header>

      <main class="centered-content">
        <app-schedule-form [hidden]="!showForm" (submitForm)="handleFormSubmit($event)"></app-schedule-form>
        <app-chat #chat [hidden]="showForm"></app-chat>
      </main>
    </div>
  `,
  styles: [`
    .app-container {
      height: 100vh;
      width: 100vw;
      display: flex;
      flex-direction: column;
      background: #020617;
      color: #f8fafc;
      font-family: 'Inter', sans-serif;
    }

    .top-nav {
      height: 70px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0 40px;
      border-bottom: 1px solid #1e293b;
      background: rgba(15, 23, 42, 0.8);
      backdrop-filter: blur(12px);
      z-index: 100;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .brand h1 {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.025em;
      margin: 0;
      background: linear-gradient(to right, #818cf8, #c084fc);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .sparkle { font-size: 1.4rem; }

    .schedule-btn {
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: white;
      border: none;
      padding: 10px 20px;
      border-radius: 20px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
    }

    .schedule-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
      filter: brightness(1.1);
    }

    .centered-content {
      flex: 1;
      display: flex;
      justify-content: center;
      padding: 30px 20px;
      overflow-y: auto;
    }

    app-chat, app-schedule-form {
      width: 100%;
      max-width: 680px;
      height: fit-content;
      min-height: min-content;
    }
  `]
})
export class AppComponent {
  @ViewChild('chat') chat!: ChatComponent;
  showForm = false;

  toggleForm() {
    this.showForm = !this.showForm;
  }

  handleFormSubmit(formData: any) {
    this.showForm = false;
    // Construct a rich prompt from the form data
    const attendeeStr = formData.attendees
      .map((a: any) => `${a.name} [${a.department}] (EID: ${a.id}) (${a.importance})`)
      .join(', ');
    
    const prompt = `[STRUCTURED FORM SUBMISSION] 
I have all the details for a new meeting:
Topic: ${formData.subject || 'Meeting'}
Team: ${formData.team}
Attendees: ${attendeeStr}
Date: ${formData.date}
Time: ${formData.time}
Timezone: ${formData.timezone}
Duration: ${formData.duration} minutes
Recurrence: ${formData.recurrence || 'once'}
Room: ${formData.room || 'Not specified'}
Location/Link: ${formData.location || 'Not specified'}
Presenter: ${formData.presenter || 'Organizer'}
EID Verification: All IDs provided are pre-verified. Trust them explicitly.

THIS IS A FINAL BOOKING COMMAND. USE THE PROVIDED EIDs DIRECTLY. DO NOT SEARCH. DO NOT ASK QUESTIONS. PROVIDE 3 TITLE OPTIONS AND THE AGENDA IMMEDIATELY.`;

    // Wait for chat component to be ready in the next tick
    setTimeout(() => {
      if (this.chat) {
        this.chat.sendAction(prompt);
      }
    }, 100);
  }
}
