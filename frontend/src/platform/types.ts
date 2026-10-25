export interface User { id: string; username: string; role: string }
export interface RepositoryStats { stars: number; forks: number; updated_at: string | null; source: 'github' | 'fallback' }
export interface Question { id: string; text: string; category?: string; main_index?: number; is_followup?: boolean }
export interface Resume {
  id: string; filename: string; status: 'queued' | 'processing' | 'ready' | 'failed';
  parsed?: Record<string, unknown> | null;
  demo?: boolean;
  diagnosis?: { id: string; status: 'queued' | 'processing' | 'ready' | 'failed';
    result?: { resume_score: number; suggestions: string[]; demo?: boolean } | null; error?: string | null } | null;
  error?: string | null; created_at: string;
}
export interface Message { id: string; role: string; content: string }
export interface Report {
  overall_score: number | null;
  dimensions: Record<string, number | null>;
  completion: { asked: number; total: number; answered: number; skipped: number };
  turns: { question: string; answer: string; score: number | null; feedback: string; is_followup?: boolean }[];
  suggestions: string[]; summary: string; demo?: boolean;
}
export interface Interview {
  id: string; resume_id: string; status: string; version: number;
  current_question: Question | null; messages: Message[];
  evaluations: Record<string, unknown>[]; report?: Report | null; error?: string | null;
  demo?: boolean; created_at: string; pending_command?: { request_id: string; status: string; error?: string; type?: string; content?: string } | null;
}
export interface InterviewCommand {
  request_id: string; type: 'start' | 'answer' | 'skip' | 'rephrase' | 'end';
  expected_question_id: string | null; version: number; content?: string;
}
export interface InterviewEvent { id: string; sequence: number; payload: { type: string; snapshot?: Interview } }
export interface Agent { version: number; id: string; name: string; scene?: string | null; provider_id?: string | null; prompt?: string; tools?: string[]; enabled?: boolean }
export interface Chat { id: string; title: string; agent_id?: string | null; messages?: Message[]; created_at?: string }
export interface Capabilities { demo_mode?: boolean; asr: boolean; tts: boolean; vision: boolean }
export interface AdminCapabilities extends Capabilities { scenes: Record<string, { enabled: boolean; agent_id: string | null }> }
