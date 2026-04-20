import { Component, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatComponent } from './chat/chat';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, ChatComponent],
  template: `
    <div class="app-container">
      <header class="top-nav">
        <div class="brand">
          <span class="sparkle">✨</span>
          <h1>AI Butler</h1>
        </div>
      </header>

      <main class="centered-content">
        <app-chat #chat></app-chat>
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
}
