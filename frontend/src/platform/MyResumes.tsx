import { Link } from 'react-router-dom';
import { useResource } from './hooks';
import type { Resume } from './types';
import { Empty, Loading, Notice, Status } from './ui';

export function MyResumesPage() {
  const resumes = useResource<Resume[]>('/resumes', 5000);
  return <>
    <div className="page-heading compact"><span className="eyebrow">MY RESUMES</span><h1>我的简历</h1><p className="muted">查看解析后的简历，按需诊断或开始模拟面试。</p></div>
    <section className="card" aria-label="我的简历列表">
      <div className="section-heading"><h2>全部简历</h2><Link className="btn primary" to="/resumes">上传简历</Link></div>
      {resumes.error && <Notice>{resumes.error}<button className="text-button" onClick={() => void resumes.refresh()}>重新加载</button></Notice>}
      {!resumes.data && !resumes.error ? <Loading /> : resumes.data && (resumes.data.length ? <div className="resume-list">{resumes.data.map(resume => <Link className="resume-row" key={resume.id} to={`/resumes/${resume.id}`}><strong>{resume.filename}</strong><Status value={resume.status} /></Link>)}</div> : <Empty title="还没有简历">上传一份简历，查看评分与改进建议。</Empty>)}
    </section>
  </>;
}
