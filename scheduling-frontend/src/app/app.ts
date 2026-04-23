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
      background: #ffffff;
      color: #1e293b;
      font-family: 'Inter', sans-serif;
    }

    .top-nav {
      height: 70px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0 40px;
      border-bottom: 1px solid #f1f5f9;
      background: #ffffff;
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
      color: #1e293b;
    }

    .sparkle { font-size: 1.4rem; color: #2563eb; }

    .centered-content {
      flex: 1;
      display: flex;
      justify-content: center;
      padding: 30px 20px;
      overflow-y: auto;
      background: #ffffff;
    }

    app-chat {
      width: 100%;
      max-width: 850px;
      height: 100%;
    }
  `]
})
export class AppComponent {
  @ViewChild('chat') chat!: ChatComponent;
}
