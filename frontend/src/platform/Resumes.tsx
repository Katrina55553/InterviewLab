import { useNavigate } from 'react-router-dom';
import { ResumeUpload } from './ResumeUpload';

export function ResumesPage() {
  const navigate = useNavigate();
  return <>
    <div className="page-heading compact"><span className="eyebrow">RESUME REVIEW</span><h1>简历诊断</h1><p className="muted">了解简历的优势和不足，获得具体的修改建议。</p></div>
    <section className="card upload-card"><h2>上传并解析简历</h2><p className="muted">解析后可以按需生成诊断，也可以直接开始模拟面试。</p><ResumeUpload onUploaded={resume => navigate(`/resumes/${resume.id}`)} /></section>
  </>;
}
