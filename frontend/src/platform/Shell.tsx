import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from './auth';
import { errorMessage } from './api';
import { Icon, Loading, Notice } from './ui';
import type { User } from './types';

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
  return <div className="auth-page bg-noise"><div className="auth-story"><div className="brand"><span className="brand-icon"><Icon name="spark" /></span>面试研习室 <small>AI MEETING</small></div><div className="auth-editorial"><span className="eyebrow">PREPARE WITH PURPOSE</span><h1>把每一次准备，<br />变成<span>更好的回答。</span></h1><p>从一份简历出发，在真实的问答里发现优势，<br />练习表达，看见每一步成长。</p><div className="auth-steps"><span>01 简历解析</span><i /><span>02 模拟面试</span><i /><span>03 复盘提升</span></div></div><p className="muted small">你的下一次面试，从这里开始。</p></div><div className="auth-form-wrap"><form className="auth-form" onSubmit={submit}><span className="eyebrow">YOUR NEXT CHAPTER</span><h2>{mode === 'login' ? '欢迎回来' : '开始你的练习'}</h2><p className="muted">{mode === 'login' ? '登录，继续积累属于你的面试经验。' : '创建账号，保存简历、对话和每一次进步。'}</p><label>用户名<input autoComplete="username" minLength={3} maxLength={64} required value={username} onChange={event => setUsername(event.target.value)} placeholder="你的用户名" /></label><label>密码<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={8} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} placeholder="至少 8 个字符" /></label>{error && <Notice>{error}</Notice>}<button className="btn primary wide" disabled={busy}>{busy ? '请稍候…' : mode === 'login' ? '登录研习室' : '创建账号'}<Icon name="arrow" size={18} /></button><p className="auth-switch">{mode === 'login' ? '还没有账号？' : '已经有账号？'}<button type="button" className="text-button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? '创建一个' : '去登录'}</button></p></form></div></div>;
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

function SidebarContent({ user, busy, onLeave, onNavigate, onClose }: {
  user: User | null;
  busy: boolean;
  onLeave: () => void;
  onNavigate?: () => void;
  onClose?: () => void;
}) {
  return <div className="sidebar-content">
    <div className="sidebar-brand-row">
      <NavLink className="brand sidebar-brand" to="/" onClick={onNavigate}>
        <span className="brand-icon"><Icon name="spark" /></span>
        <span>面试研习室<small>AI MEETING</small></span>
      </NavLink>
      {onClose && <button type="button" className="sidebar-icon-button" aria-label="关闭导航菜单" onClick={onClose}><Icon name="close" /></button>}
    </div>
    <nav className="sidebar-nav" aria-label="主导航">
      <span className="sidebar-section-label">我的研习室</span>
      <NavLink end to="/" onClick={onNavigate}><Icon name="dashboard" size={19} /><span>工作台</span></NavLink>
      <NavLink to="/resumes" onClick={onNavigate}><Icon name="file" size={19} /><span>简历诊断</span></NavLink>
      <NavLink to="/interviews/new" onClick={onNavigate}><Icon name="chat" size={19} /><span>模拟面试</span></NavLink>
      <NavLink to="/history" onClick={onNavigate}><Icon name="clock" size={19} /><span>面试记录</span></NavLink>
      <NavLink to="/chat" onClick={onNavigate}><Icon name="chat" size={19} /><span>AI 对话</span></NavLink>
      {user?.role === 'admin' && <><span className="sidebar-section-label sidebar-admin-label">管理</span><NavLink to="/admin" onClick={onNavigate}><Icon name="settings" size={19} /><span>管理设置</span></NavLink></>}
    </nav>
    <div className="sidebar-bottom">
      <div className="sidebar-note"><Icon name="spark" size={17} /><p>认真准备，自信表达。<span>每一次练习，都更进一步。</span></p></div>
      <div className="profile">
        <span className="avatar">{user?.username.slice(0, 1).toUpperCase()}</span>
        <div className="profile-details"><strong title={user?.username}>{user?.username}</strong><span>{user?.role === 'admin' ? '管理员' : '个人账号'}</span></div>
        <button type="button" className="sidebar-logout" onClick={onLeave} disabled={busy}><Icon name="logout" size={17} /><span>退出</span></button>
      </div>
    </div>
  </div>;
}

function Shell() {
  const { user, logout } = useAuth();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDialogElement>(null);
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
  const sidebarProps = { user, busy, onLeave: () => { closeMenu(); void leave() } };
  return <div className="platform bg-noise">
    <aside className="app-sidebar" aria-label="研习室侧边栏"><SidebarContent {...sidebarProps} /></aside>
    <header className="mobile-topbar">
      <NavLink className="brand" to="/"><span className="brand-icon"><Icon name="spark" /></span>面试研习室</NavLink>
      <button type="button" className="sidebar-icon-button" aria-label="打开导航菜单" aria-controls="mobile-navigation" aria-expanded={menuOpen} onClick={() => { menu.current?.showModal(); setMenuOpen(true) }}><Icon name="menu" /></button>
    </header>
    <dialog ref={menu} id="mobile-navigation" className="sidebar-dialog" aria-label="导航菜单" onClose={() => setMenuOpen(false)} onClick={event => { if (event.target === event.currentTarget) closeMenu() }}>
      <SidebarContent {...sidebarProps} onNavigate={closeMenu} onClose={closeMenu} />
    </dialog>
    <div className="platform-content">
      <main className="page">{error && <Notice>{error}</Notice>}<Outlet /></main>
      <footer className="footer"><span>面试研习室</span><span>认真准备，自信表达。</span><span>AI 建议仅供练习参考</span></footer>
    </div>
  </div>;
}
