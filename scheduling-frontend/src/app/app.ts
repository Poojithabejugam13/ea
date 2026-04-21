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
      color: #171717;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    .top-nav {
      display: none;
    }

    .centered-content {
      flex: 1;
      display: flex;
      justify-content: center;
      padding: 0;
      overflow: hidden;
    }

    app-chat, app-schedule-form {
      width: 100%;
      height: 100%;
      max-width: 100%;
    }
  `]
})
export class AppComponent {
  @ViewChild('chat') chat!: ChatComponent;
}
