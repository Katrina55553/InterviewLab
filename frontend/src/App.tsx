import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AdminPage } from './platform/Admin';
import { InterviewRoomLayout } from './platform/Camera';
import { ChatPage } from './platform/Chat';
import { CommunityPage } from './platform/Community';
import { HistoryPage } from './platform/History';
import { InterviewRoomPage } from './platform/InterviewRoom';
import { ResumesPage } from './platform/Resumes';
import { MyResumesPage } from './platform/MyResumes';
import { InterviewPage } from './platform/Interview';
import { PreparationPage } from './platform/Preparation';
import { ReportPage } from './platform/Report';
import { AuthPage, SessionGate } from './platform/Shell';
import './platform/platform.css';

export default function App() {
  return <BrowserRouter><Routes><Route path="/login" element={<AuthPage />} /><Route element={<SessionGate />}><Route index element={<Navigate to="/resumes" replace />} /><Route path="history" element={<HistoryPage />} /><Route path="my-resumes" element={<MyResumesPage />} /><Route path="resumes" element={<ResumesPage />} /><Route path="resumes/:id" element={<PreparationPage />} /><Route path="interviews" element={<InterviewRoomLayout />}><Route path="new" element={<InterviewRoomPage />} /><Route path=":id" element={<InterviewPage />} /></Route><Route path="interviews/:id/report" element={<ReportPage />} /><Route path="chat" element={<ChatPage />} /><Route path="chat/:id" element={<ChatPage />} /><Route path="community" element={<CommunityPage />} /><Route path="admin" element={<AdminPage />} /></Route><Route path="*" element={<Navigate to="/" replace />} /></Routes></BrowserRouter>;
}
