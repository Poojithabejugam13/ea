import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private http = inject(HttpClient);
  private baseUrl = 'http://127.0.0.1:8000';

  // Agent Process
  processMessage(prompt: string, sessionId: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/agent/process`, {
      prompt,
      session_id: sessionId,
      // Treating the current UI user as Poojitha (103) until Teams Login is built
      user_id: "103",
    });
  }

  getAgentStatus(sessionId: string): Observable<{ message: string }> {
    return this.http.get<{ message: string }>(`${this.baseUrl}/agent/status?session_id=${sessionId}`);
  }

  // Search
  searchUsers(query: string, teams: string[]): Observable<any[]> {
    return this.http.post<any[]>(`${this.baseUrl}/search/users`, { query, teams });
  }

  getTeams(): Observable<string[]> {
    return this.http.get<string[]>(`${this.baseUrl}/teams`);
  }

  getSubjectSuggestions(query: string = ""): Observable<string[]> {
    return this.http.get<string[]>(`${this.baseUrl}/suggestions/subjects?query=${query}`);
  }

  getRoomSuggestions(query: string = "", start?: string, end?: string): Observable<string[]> {
    let url = `${this.baseUrl}/suggestions/rooms?query=${query}`;
    if (start) url += `&start=${start}`;
    if (end) url += `&end=${end}`;
    return this.http.get<string[]>(url);
  }

  getLocationSuggestions(query: string = ""): Observable<string[]> {
    return this.http.get<string[]>(`${this.baseUrl}/suggestions/locations?query=${query}`);
  }

  // Preferences
  getPrefs(): Observable<any> {
    return this.http.get(`${this.baseUrl}/prefs`);
  }

  savePrefs(prefs: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/prefs`, prefs);
  }

  // Meetings
  getMeetings(): Observable<any> {
    return this.http.get(`${this.baseUrl}/meetings`);
  }

  deleteMeeting(fingerprint: string, eventId: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/meetings/delete`, {
      fingerprint, event_id: eventId
    });
  }
}
