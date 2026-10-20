import { Link, useNavigate } from 'react-router-dom';
import { useResource } from './hooks';
import { ResumeUpload } from './ResumeUpload';
import type { Resume } from './types';
import { Empty, Loading, Notice, Status } from './ui';

export function ResumesPage() {
  const resumes = useResource<Resume[]>('/resumes', 5000);
  const navigate = useNavigate();
  return <>
    <div className="page-heading compact"><span className="eyebrow">RESUME REVIEW</span><h1>简历诊断</h1><p className="muted">了解简历的优势和不足，获得具体的修改建议。</p></div>
    <div className="resumes-grid">
      <section className="card upload-card"><h2>上传简历，获取诊断</h2><ResumeUpload onUploaded={resume => navigate(`/resumes/${resume.id}`)} /></section>
      <section className="card"><h2>我的简历</h2>
        {resumes.error && <Notice>{resumes.error}<button className="text-button" onClick={() => void resumes.refresh()}>重新加载</button></Notice>}
        {!resumes.data && !resumes.error ? <Loading /> : !resumes.data?.length ? <Empty title="还没有简历">上传一份简历，查看评分与改进建议。</Empty> : <div className="resume-list">{resumes.data.map(resume => <Link className="resume-row" key={resume.id} to={`/resumes/${resume.id}`}><strong>{resume.filename}</strong><Status value={resume.status} /></Link>)}</div>}
      </section>
    </div>
  </>;
}
