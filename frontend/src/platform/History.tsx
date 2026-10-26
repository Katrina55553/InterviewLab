import { useState } from 'react';
import { Link } from 'react-router-dom';
import { DeleteRecordButton } from './DeleteRecord';
import { useDeletableList } from './hooks';
import { formatDate } from './format';
import type { Interview } from './types';
import { Empty, Icon, Loading, Notice, Status } from './ui';

export function InterviewList({ items, loading = false, onDeleted }: { items: Interview[]; loading?: boolean; onDeleted?: (id: string) => void }) {
  if (loading) return <Loading />;
  if (!items.length) return <Empty title="还没有面试记录">进入面试室，上传或选择简历，开启第一次模拟面试。</Empty>;
  return <div className="interview-list">{items.map((item, index) => <div className="record-row" key={item.id}>
    <Link className="interview-row" to={item.status === 'completed' ? `/interviews/${item.id}/report` : `/interviews/${item.id}`}><span className="row-number">{String(index + 1).padStart(2, '0')}</span><div><strong>模拟面试 <span className="mono muted">#{item.id.slice(0, 6)}</span></strong><small>{formatDate(item.created_at)}</small></div><Status value={item.status} /><span className="row-action">{item.status === 'completed' ? '查看报告' : '继续练习'}<Icon name="arrow" size={16} /></span></Link>
    {onDeleted && <DeleteRecordButton kind="interview" id={item.id} name={`模拟面试 #${item.id.slice(0, 6)}`} onDeleted={onDeleted} />}
  </div>)}</div>;
}

export function HistoryPage() {
  const interviews = useDeletableList<Interview>('/interviews');
  const [filter, setFilter] = useState('all');
  const items = interviews.data?.filter(item => filter === 'all' || (filter === 'completed' ? item.status === 'completed' : item.status !== 'completed')) ?? [];
  return <><div className="page-heading"><span className="eyebrow">YOUR INTERVIEW JOURNAL</span><h1>每一次练习，都算数。</h1><p className="muted">回到你的回答，发现变化，积累下一次的自信。</p></div><section className="card"><div className="section-heading"><div className="tabs" aria-label="面试记录筛选">{[['all', '全部记录'], ['active', '未完成'], ['completed', '已完成']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><button className="text-button" onClick={() => void interviews.refresh()}>刷新</button></div>{interviews.error && <Notice>{interviews.error}</Notice>}<InterviewList items={items} loading={!interviews.data && !interviews.error} onDeleted={interviews.onDeleted} /></section></>;
}
