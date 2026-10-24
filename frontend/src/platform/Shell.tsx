import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, matchPath, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './auth';
import { errorMessage } from './api';
import { Icon, Loading, Notice } from './ui';
import type { User } from './types';
import { RepositoryBadge } from './RepositoryBadge';

const sidebarStorageKey = 'meeting:sidebar-collapsed';

const navigationGroups = [
  { label: '工作空间', items: [
    { to: '/my-resumes', label: '我的简历', icon: 'file', paths: ['/my-resumes'] },
    { to: '/history', label: '面试记录', icon: 'clipboard', paths: ['/history', '/interviews/:id/report'] },
  ] },
  { label: '工具与练习', items: [
    { to: '/resumes', label: '简历诊断', icon: 'scan', paths: ['/resumes', '/resumes/:id'] },
    { to: '/chat', label: 'JD 分析', icon: 'briefcase', paths: ['/chat', '/chat/:id'] },
    { to: '/interviews/new', label: '模拟面试', icon: 'headphones', paths: ['/interviews/:id'] },
    { to: '/community', label: '面经社区', icon: 'chat', paths: ['/community'] },
  ] },
  { label: '更多', adminOnly: true, items: [
    { to: '/admin', label: '管理设置', icon: 'settings', paths: ['/admin'] },
  ] },
] as const;

export function AuthPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { user, authenticate } = useAuth();
  const navigate = useNavigate();
  if (user) return <Navigate to="/" replace />;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try { await authenticate(mode, username.trim(), password); navigate('/', { replace: true }) }
    catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  return <div className="auth-page bg-noise"><div className="auth-story"><div className="brand"><span className="brand-icon"><Icon name="spark" /></span>知面 <small>InterviewLab</small></div><div className="auth-editorial"><span className="eyebrow">PREPARE WITH PURPOSE</span><h1>把每一次准备，<br />变成<span>更好的回答。</span></h1><p>从一份简历出发，在真实的问答里发现优势，<br />练习表达，看见每一步成长。</p><div className="auth-steps"><span>01 简历解析</span><i /><span>02 模拟面试</span><i /><span>03 复盘提升</span></div></div><p className="muted small">你的下一次面试，从这里开始。</p></div><div className="auth-form-wrap"><form className="auth-form" onSubmit={submit}><span className="eyebrow">YOUR NEXT CHAPTER</span><h2>{mode === 'login' ? '欢迎回来' : '开始你的练习'}</h2><p className="muted">{mode === 'login' ? '登录，继续积累属于你的面试经验。' : '创建账号，保存简历、对话和每一次进步。'}</p><label>用户名<input autoComplete="username" minLength={3} maxLength={64} required value={username} onChange={event => setUsername(event.target.value)} placeholder="你的用户名" /></label><label>密码<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={8} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} placeholder="至少 8 个字符" /></label>{error && <Notice>{error}</Notice>}<button className="btn primary wide" disabled={busy}>{busy ? '请稍候…' : mode === 'login' ? '登录知面' : '创建账号'}<Icon name="arrow" size={18} /></button><p className="auth-switch">{mode === 'login' ? '还没有账号？' : '已经有账号？'}<button type="button" className="text-button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? '创建一个' : '去登录'}</button></p></form></div></div>;
}

export function SessionGate() {
  const { loading, user, error, refresh } = useAuth();
  useEffect(() => { void refresh() }, [refresh]);
  useEffect(() => {
    const expired = () => useAuth.setState({ user: null, loading: false, error: '' });
    window.addEventListener('meeting:session-expired', expired);
    return () => window.removeEventListener('meeting:session-expired', expired);
  }, []);
  if (loading) return <div className="full-state"><Loading text="正在打开研习室…" /></div>;
  if (error) return <div className="full-state"><Notice>{error}</Notice><button className="btn" onClick={() => void refresh()}>重新连接</button></div>;
  return user ? <Shell /> : <Navigate to="/login" replace />;
}

function SidebarContent({ user, busy, activePath, onLeave, onNavigate, onClose }: {
  user: User | null;
  busy: boolean;
  activePath?: string;
  onLeave: () => void;
  onNavigate?: () => void;
  onClose?: () => void;
}) {
  return <div className="sidebar-content">
    <div className="sidebar-brand-row">
      <Link className="brand sidebar-brand" to="/" onClick={onNavigate}>
        <span className="brand-icon"><Icon name="spark" size={26} /></span>
        <span>知面<small>InterviewLab</small></span>
      </Link>
      {onClose && <button type="button" className="sidebar-icon-button" aria-label="关闭导航菜单" onClick={onClose}><Icon name="close" /></button>}
    </div>
    <nav className="sidebar-nav" aria-label="主导航">
      {navigationGroups.filter(group => !('adminOnly' in group) || user?.role === 'admin').map(group => <div className="sidebar-nav-group" role="group" aria-label={group.label} key={group.label}>
        <p className="sidebar-section-label">{group.label}</p>
        {group.items.map(item => <Link key={item.to} to={item.to} className={activePath === item.to ? 'active' : undefined} aria-current={activePath === item.to ? 'page' : undefined} onClick={onNavigate}>
          <Icon name={item.icon} size={22} /><span>{item.label}</span>
          {activePath === item.to && <span className="sidebar-active-dot" aria-hidden="true" />}
        </Link>)}
      </div>)}
    </nav>
    <div className="sidebar-bottom">
      <div className="profile">
        <span className="avatar">{user?.username.slice(0, 1).toUpperCase()}</span>
        <div className="profile-details"><strong title={user?.username}>{user?.username}</strong><span>{user?.role === 'admin' ? '管理员' : '个人账号'}</span></div>
        <button type="button" className="sidebar-logout" onClick={onLeave} disabled={busy}><Icon name="logout" size={17} /><span>退出</span></button>
      </div>
    </div>
  </div>;
}

function Shell() {
  const { pathname } = useLocation();
  const isChat = pathname === '/chat' || pathname.startsWith('/chat/');
  const { user, logout } = useAuth();
  const groups = navigationGroups.filter(group => !('adminOnly' in group) || user?.role === 'admin');
  const currentGroup = groups.find(group => group.items.some(item => item.paths.some(path => matchPath(path, pathname))));
  const currentItem = currentGroup?.items.find(item => item.paths.some(path => matchPath(path, pathname)));
  const detail = matchPath('/resumes/:id', pathname) ? '诊断结果'
    : matchPath('/interviews/:id/report', pathname) ? '面试报告'
    : pathname !== '/interviews/new' && matchPath('/interviews/:id', pathname) ? '面试详情' : null;
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem(sidebarStorageKey) === 'true' }
    catch { return false }
  });
  const menu = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    try { localStorage.setItem(sidebarStorageKey, String(sidebarCollapsed)) }
    catch { /* Navigation still works when browser storage is unavailable. */ }
  }, [sidebarCollapsed]);
  useEffect(() => {
    const closeOnWideScreen = () => { if (window.innerWidth > 850 && menu.current?.open) menu.current.close() };
    window.addEventListener('resize', closeOnWideScreen);
    return () => window.removeEventListener('resize', closeOnWideScreen);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = overflow };
  }, [menuOpen]);
  const closeMenu = () => menu.current?.close();
  const leave = async () => { setBusy(true); try { await logout() } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) } };
  const sidebarProps = { user, busy, activePath: currentItem?.to, onLeave: () => { closeMenu(); void leave() } };
  return <div className={`platform bg-noise${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
    <aside id="app-navigation" className="app-sidebar" aria-label="研习室侧边栏" aria-hidden={sidebarCollapsed} inert={sidebarCollapsed}><SidebarContent {...sidebarProps} /></aside>
    <dialog ref={menu} id="mobile-navigation" className="sidebar-dialog" aria-label="导航菜单" onClose={() => setMenuOpen(false)} onClick={event => { if (event.target === event.currentTarget) closeMenu() }}>
      <SidebarContent {...sidebarProps} onNavigate={closeMenu} onClose={closeMenu} />
    </dialog>
    <div className={`platform-content${isChat ? ' chat-workspace' : ''}`}>
      <header className="workspace-topbar">
        <button type="button" className="sidebar-icon-button workspace-sidebar-toggle" aria-label={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'} title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'} aria-controls="app-navigation" aria-expanded={!sidebarCollapsed} onClick={() => setSidebarCollapsed(collapsed => !collapsed)}><Icon name="sidebar" /></button>
        <button type="button" className="sidebar-icon-button workspace-menu-button" aria-label="打开导航菜单" aria-controls="mobile-navigation" aria-expanded={menuOpen} onClick={() => { menu.current?.showModal(); setMenuOpen(true) }}><Icon name="menu" /></button>
        {currentGroup && currentItem && <nav className="workspace-breadcrumb" aria-label="面包屑导航"><ol>
          <li>{currentGroup.label}</li>
          <li><Icon name="chevron" size={16} />{detail ? <Link to={currentItem.to}>{currentItem.label}</Link> : <span aria-current="page">{currentItem.label}</span>}</li>
          {detail && <li><Icon name="chevron" size={16} /><span aria-current="page">{detail}</span></li>}
        </ol></nav>}
        <RepositoryBadge />
      </header>
      <main className={`page${isChat ? ' page-chat' : ''}`}>{error && <Notice>{error}</Notice>}<Outlet /></main>
      <footer className="footer"><span>知面</span><span>认真准备，自信表达。</span><span>AI 建议仅供练习参考</span></footer>
    </div>
  </div>;
}
