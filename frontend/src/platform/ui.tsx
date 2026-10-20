import type { ReactNode } from 'react';
import Markdown from 'react-markdown';

export function Icon({ name, size = 20 }: { name: 'arrow' | 'upload' | 'chat' | 'file' | 'spark' | 'settings' | 'mic' | 'camera' | 'volume' | 'close' | 'check' | 'clock' | 'plus'; size?: number }) {
  const paths = {
    arrow: 'M5 12h14m-6-6 6 6-6 6', upload: 'M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4',
    chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10A9.5 9.5 0 0 1 12 2a9 9 0 0 1 9 9.5Z',
    file: 'M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8m-8 4h6', spark: 'm12 2 2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4L12 2Z',
    settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-8 4H2m20 0h-2M12 4V2m0 20v-2M6 6 4 4m16 16-2-2M6 18l-2 2M20 4l-2 2',
    mic: 'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Zm-3 6v1a6 6 0 0 0 12 0v-1M12 18v4m-4 0h8',
    camera: 'M8 5 10 2h4l2 3h6v15H2V5h6Zm4 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
    volume: 'm11 4-6 5H2v6h3l6 5V4Zm5 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14',
    close: 'm6 6 12 12M6 18 18 6', check: 'm5 12 4 4L19 6', clock: 'M12 8v5l3 2m6-3a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z', plus: 'M12 4v16M4 12h16',
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
export function Notice({ children, tone = 'error' }: { children: ReactNode; tone?: 'error' | 'info' | 'success' }) {
  return <div className={`notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div>;
}
export function Loading({ text = '正在加载…' }: { text?: string }) { return <div className="loading" role="status"><span className="spinner" />{text}</div> }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><Icon name="file" size={30} /><h3>{title}</h3><p>{children}</p></div> }
export function DemoBadge({ demo }: { demo?: boolean }) { return demo ? <span className="badge badge-amber">演示模式 · 非真实 AI 评估</span> : null }
export function RichText({ text }: { text: string }) { return <div className="rich-text"><Markdown>{text}</Markdown></div> }
const statusLabel: Record<string, string> = { queued: '排队中', processing: '准备中', ready: '准备就绪', draft: '待开始', active: '进行中', interviewing: '进行中', waiting: '等待回答', waiting_answer: '等待回答', waiting_for_answer: '等待回答', reporting: '生成报告中', completed: '已完成', failed: '处理失败', running: '处理中' };
export function Status({ value }: { value: string }) { return <span className={`badge ${value === 'failed' ? 'badge-red' : ['ready', 'completed'].includes(value) ? 'badge-green' : ''}`}><span className="status-dot" />{statusLabel[value] ?? value}</span> }
