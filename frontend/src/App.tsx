import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AdminPage } from './platform/Admin';
import { ChatPage } from './platform/Chat';
import { Dashboard, HistoryPage } from './platform/Dashboard';
import { InterviewPage } from './platform/Interview';
import { PreparationPage } from './platform/Preparation';
import { ReportPage } from './platform/Report';
import { AuthPage, SessionGate } from './platform/Shell';
import './platform/platform.css';

export default function App() {
  return <BrowserRouter><Routes><Route path="/login" element={<AuthPage />} /><Route element={<SessionGate />}><Route index element={<Dashboard />} /><Route path="history" element={<HistoryPage />} /><Route path="resumes/:id" element={<PreparationPage />} /><Route path="interviews/:id" element={<InterviewPage />} /><Route path="interviews/:id/report" element={<ReportPage />} /><Route path="chat" element={<ChatPage />} /><Route path="chat/:id" element={<ChatPage />} /><Route path="admin" element={<AdminPage />} /></Route><Route path="*" element={<Navigate to="/" replace />} /></Routes></BrowserRouter>;
}
