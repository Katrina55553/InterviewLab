import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, errorMessage, requestId } from './api';
import { useAuth } from './auth';
import { directions, outcomes } from './community-data';
import type { CommunityPost, CommunityDraft, CommunityFeed, CommunityMeta, CommentPage, DraftResult, CommunityComment, Direction, Outcome } from './community-data';
import { useCommunityResource } from './community-api';
import { Icon, Loading, Notice, RichText } from './ui';
import { Select } from './Select';
import './community.css';

type FeedTab = 'recommended' | 'latest' | 'liked' | 'mine';
const newDraft = (): CommunityDraft => ({ id: requestId(), title: '', company: '', role: '', direction: '前端开发', stage: '校招 · 一面', outcome: '持续复盘', tags: '', content: '' });
const pageSize = 5;
function dateLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', timeZone: 'Asia/Shanghai' }).format(new Date(value));
}

function Modal({ title, className = '', busy = false, onClose, children }: { title: string; className?: string; busy?: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    dialog.querySelector<HTMLElement>('[data-community-focus]')?.focus();
    return () => { dialog.close(); document.body.style.overflow = previousOverflow };
  }, []);
  return <dialog ref={ref} className={`community-dialog ${className}`} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose() }} onClick={event => { if (!busy && event.target === event.currentTarget) onClose() }}>
    <div className="community-dialog-inner"><header className="community-dialog-header"><span><Icon name="book" size={18} />{title}</span><button type="button" className="community-icon-button" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>{children}</div>
  </dialog>;
}

function PostCard({ post, busy, onOpen, onLike }: { post: CommunityPost; busy: boolean; onOpen: () => void; onLike: () => void }) {
  return <article className={`community-post${post.featured ? ' community-post-featured' : ''}`}>
    <div className="community-post-meta"><span className={`community-company company-${post.direction === '前端开发' ? 'mint' : post.direction === '后端开发' ? 'blue' : 'peach'}`} aria-hidden="true">{post.company.slice(0, 1)}</span><span>{post.company}<span className="community-meta-divider">/</span>{post.role}</span><span className="community-post-stage">{post.stage}</span><span className={`community-outcome${post.outcome === '已拿 Offer' ? ' offer' : ''}`}>{post.outcome === '已拿 Offer' && <Icon name="check" size={12} />}{post.outcome}</span></div>
    <h2><button type="button" onClick={onOpen}>{post.featured && <span className="community-featured-label">精选</span>}{post.title}</button></h2>
    <p className="community-post-summary">{post.summary}</p><div className="community-post-tags">{post.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
    <footer className="community-post-footer"><span className="community-author"><span className={`community-avatar avatar-${post.direction === '后端开发' ? 'sand' : 'sage'}`}>{post.author.slice(0, 1)}</span><span>{post.author}</span><span className="community-post-date">{dateLabel(post.createdAt)}</span></span>
      <div className="community-post-actions"><button type="button" disabled={busy} className={post.liked ? 'is-active' : ''} aria-label={`${post.liked ? '取消点赞' : '点赞'}：${post.title}`} aria-pressed={post.liked} onClick={onLike}><Icon name="like" size={15} /><span>{post.likes}</span><span>{post.liked ? '已点赞' : '点赞'}</span></button><button type="button" aria-label={`查看评论：${post.title}`} onClick={onOpen}><Icon name="chat" size={15} /><span>{post.commentCount}</span></button></div>
    </footer>
  </article>;
}

export function CommunityPage() {
  const user = useAuth(state => state.user);
  return user ? <CommunityWorkspace key={user.id} username={user.username} /> : null;
}

function CommunityWorkspace({ username }: { username: string }) {
  const [draft, setDraft] = useState(newDraft);
  const [draftVersion, setDraftVersion] = useState(0);
  const [query, setQuery] = useState('');
  const [direction, setDirection] = useState('全部');
  const [company, setCompany] = useState('全部公司');
  const [topic, setTopic] = useState('');
  const [tab, setTab] = useState<FeedTab>('recommended');
  const [page, setPage] = useState(1);
  const [editorOpen, setEditorOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const [comment, setComment] = useState('');
  const [commentPage, setCommentPage] = useState(1);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingLikes, setPendingLikes] = useState<string[]>([]);
  const commentRequest = useRef<{ id: string; postId: string; content: string } | null>(null);
  const [params, setParams] = useSearchParams();
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const postId = params.get('post');
  const filters = new URLSearchParams({ q: query.trim(), tab, page: String(page), page_size: String(pageSize) });
  if (direction !== '全部') filters.set('direction', direction);
  if (company !== '全部公司') filters.set('company', company);
  if (topic) filters.set('topic', topic);
  const feed = useCommunityResource<CommunityFeed>(`/community/posts?${filters}`);
  const meta = useCommunityResource<CommunityMeta>('/community/meta');
  const detail = useCommunityResource<CommunityPost>(postId ? `/community/posts/${encodeURIComponent(postId)}` : null);
  const comments = useCommunityResource<CommentPage>(postId ? `/community/posts/${encodeURIComponent(postId)}/comments?page=${commentPage}` : null);
  const activePost = detail.data;
  const allCompanies = meta.data?.companies ?? [];
  const topics = meta.data?.topics.map(item => [item.tag, item.count] as const) ?? [];
  const total = feed.data?.total ?? 0;
  const currentPage = feed.data?.page ?? page;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const pageNumbers = Array.from({ length: Math.min(5, pages) }, (_, index) => Math.max(1, Math.min(currentPage - 2, pages - 4)) + index);
  const hasFilters = Boolean(query.trim() || topic || direction !== '全部' || company !== '全部公司');
  const displayError = error || feed.error || meta.error;
  const refresh = () => { feed.refresh(); meta.refresh() };

  const toggleLiked = async (post: CommunityPost) => {
    if (pendingLikes.includes(post.id)) return;
    setPendingLikes(previous => [...previous, post.id]); setError('');
    try {
      const next = await api<{ liked: boolean; likes: number }>(`/community/posts/${post.id}/like`, { method: post.liked ? 'DELETE' : 'PUT' });
      feed.update(value => ({ ...value, items: value.items.map(item => item.id === post.id ? { ...item, ...next } : item) }));
      detail.update(value => value.id === post.id ? { ...value, ...next } : value);
      refresh(); setFeedback(next.liked ? '已点赞，可在「我的点赞」中找到这篇面经。' : '已取消点赞');
    } catch (err) { setError(errorMessage(err)) }
    finally { setPendingLikes(previous => previous.filter(id => id !== post.id)) }
  };
  const selectTab = (next: FeedTab) => { setTab(next); setPage(1); setFeedback('') };
  const resetFilters = () => { setQuery(''); setDirection('全部'); setCompany('全部公司'); setTopic(''); setPage(1) };
  const openPost = (id: string) => { setComment(''); setCommentPage(1); setError(''); commentRequest.current = null; setParams(previous => { const next = new URLSearchParams(previous); next.set('post', id); return next }) };
  const closePost = () => { setParams(previous => { const next = new URLSearchParams(previous); next.delete('post'); return next }) };
  const openEditor = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const saved = await api<DraftResult>('/community/draft');
      setDraft(saved.draft ?? newDraft()); setDraftVersion(saved.version); setPreview(false); setEditorOpen(true);
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  const changeDraft = (next: CommunityDraft) => { setDraft(next); setError('') };
  const saveDraftAndClose = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const saved = await api<DraftResult>('/community/draft', { method: 'PUT', body: { ...draft, version: draftVersion } });
      setDraftVersion(saved.version); setEditorOpen(false); setFeedback('草稿已保存到账号，下次打开可继续编辑。');
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  const publish = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!draft.title.trim() || !draft.company.trim() || !draft.role.trim() || draft.content.trim().length < 20) { setError('请填写标题、公司、岗位，以及至少 20 字的面经正文。'); return }
    const tags = [...new Set(draft.tags.split(/[,，、\s]+/).filter(Boolean))].slice(0, 5);
    setBusy(true); setError('');
    try {
      await api<CommunityPost>('/community/posts', { method: 'POST', body: { ...draft, tags: tags.length ? tags : [draft.direction], draftVersion } });
      setDraft(newDraft()); setDraftVersion(0); setEditorOpen(false); resetFilters(); setTab('mine'); refresh(); setFeedback('面经已发布，其他登录用户现在可以阅读、点赞和评论。');
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  const addComment = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !activePost || !comment.trim()) return;
    const content = comment.trim();
    if (commentRequest.current?.postId !== activePost.id || commentRequest.current?.content !== content) commentRequest.current = { id: requestId(), postId: activePost.id, content };
    setBusy(true); setError('');
    try {
      await api<CommunityComment>(`/community/posts/${activePost.id}/comments`, { method: 'POST', body: { id: commentRequest.current.id, content } });
      setComment(''); commentRequest.current = null; setCommentPage(Math.max(1, Math.ceil((activePost.commentCount + 1) / 30))); comments.refresh(); detail.refresh(); feed.refresh();
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };

  return <div className="community-page">
    <div className="community-page-heading"><div><span className="community-eyebrow"><span />GROW TOGETHER</span><h1>面经社区<span>每一份经历，都有值得分享的价值。</span></h1></div><button type="button" className="btn primary community-publish" disabled={busy} onClick={() => void openEditor()}><Icon name="pencil" size={17} />发布面经</button></div>

    <section className="community-hero" aria-labelledby="community-hero-title">
      <div className="community-hero-copy"><span className="community-hero-kicker"><Icon name="chat" size={15} />一份面经，多一份底气</span><h2 id="community-hero-title">让经历成为经验，<br />让下一次面试<span>更从容。</span></h2><p>读一读他人的面试故事，也记录自己的成长。<br className="community-desktop-break" />从真实的问题出发，找到适合你的准备方向。</p><button type="button" className="community-hero-link" onClick={() => { searchRef.current?.focus(); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>发现值得读的面经<Icon name="arrow" size={16} /></button></div>
      <div className="community-hero-art" aria-hidden="true"><span className="community-art-circle" /><span className="community-art-spark">✳</span><div className="community-art-card art-card-back"><span>KEEP LEARNING</span><i /><i /></div><div className="community-art-card art-card-front"><div className="community-art-card-top"><span><Icon name="book" size={18} /></span><small>MY INTERVIEW NOTES</small><Icon name="like" size={17} /></div><strong>每一次复盘，<br />都是向前的一步。</strong><div className="community-art-lines"><i /><i /></div><div className="community-art-card-footer"><span>提问</span><span>思考</span><span>成长</span><b><Icon name="check" size={13} /></b></div></div><span className="community-art-sticker"><Icon name="spark" size={16} />下一站，理想 Offer</span><span className="community-art-dot" /></div>
    </section>

    <div className="community-layout"><div className="community-main" ref={listRef}>
      <section className="community-search-panel" aria-label="搜索和筛选面经"><div className="community-search-row"><div className="community-search"><Icon name="search" size={19} /><input ref={searchRef} aria-label="搜索面经" placeholder="搜索公司、岗位、面试题或关键词…" value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} />{query && <button type="button" aria-label="清空搜索" onClick={() => { setQuery(''); setPage(1); searchRef.current?.focus() }}><Icon name="close" size={15} /></button>}<kbd>搜索</kbd></div><label className="community-company-select"><span className="sr-only">筛选公司</span><Select aria-label="筛选公司" value={company} onValueChange={value => { setCompany(value); setPage(1) }} options={['全部公司', ...allCompanies].map(item => ({ value: item, label: item }))} /></label></div><div className="community-directions"><span><Icon name="filter" size={15} />方向</span><div role="group" aria-label="岗位方向">{['全部', ...directions].map(item => <button type="button" aria-pressed={direction === item} className={direction === item ? 'selected' : ''} key={item} onClick={() => { setDirection(item); setPage(1) }}>{item}</button>)}</div></div></section>

      <div className="community-feed-header"><div className="community-feed-tabs" role="group" aria-label="面经列表">{([{ id: 'recommended', label: '推荐面经', icon: 'spark' }, { id: 'latest', label: '最新发布', icon: 'clock' }, { id: 'liked', label: '我的点赞', icon: 'like' }, { id: 'mine', label: '我的面经', icon: 'pencil' }] as const).map(item => <button type="button" key={item.id} className={tab === item.id ? 'active' : ''} aria-pressed={tab === item.id} onClick={() => selectTab(item.id)}><Icon name={item.icon} size={15} />{item.label}</button>)}</div><span className="community-results" aria-live="polite">{total} 篇面经</span></div>
      {(hasFilters) && <div className="community-filter-summary"><span>正在查看{direction === '全部' ? '' : `「${direction}」`}{company === '全部公司' ? '' : `「${company}」`}{topic && `「话题：${topic}」`}{query && `包含「${query}」的`}面经</span><button type="button" onClick={resetFilters}>清除筛选<Icon name="close" size={12} /></button></div>}
      <div className="community-feedback" role="status">{feedback}</div>
      {displayError && !editorOpen && !postId && <Notice>{displayError}<button type="button" className="text-button" onClick={() => { setError(''); refresh() }}>重新加载</button></Notice>}
      <div className="community-feed">{feed.loading && !feed.data && <Loading text="正在加载面经…" />}{(feed.data?.items ?? []).map(post => <PostCard key={post.id} post={post} busy={pendingLikes.includes(post.id)} onOpen={() => openPost(post.id)} onLike={() => void toggleLiked(post)} />)}
        {!feed.loading && !feed.error && !total && <div className="community-empty"><span><Icon name={tab === 'liked' ? 'like' : tab === 'mine' ? 'pencil' : 'search'} size={27} /></span><h2>{hasFilters ? '暂时没有找到相关面经' : tab === 'liked' ? '还没有点赞的面经' : '你的经历，也能成为别人的参考'}</h2><p>{hasFilters ? '试试其他关键词，或放宽一下筛选条件。' : tab === 'liked' ? '给有帮助的面经点个赞，就能在这里找到。' : '记录面试问题、准备方法和一点点新的收获。'}</p>{hasFilters ? <button type="button" className="btn" onClick={resetFilters}>清除筛选</button> : tab === 'liked' ? <button type="button" className="btn" onClick={() => selectTab('recommended')}>去发现面经<Icon name="arrow" size={15} /></button> : <button type="button" className="btn primary" disabled={busy} onClick={() => void openEditor()}><Icon name="plus" size={16} />写下第一篇面经</button>}</div>}
      </div>
      {total > 0 && <div className="community-pagination"><span>共 {total} 篇，当前第 {currentPage} / {pages} 页</span><nav aria-label="面经分页"><button type="button" aria-label="上一页" disabled={currentPage === 1} onClick={() => { setPage(currentPage - 1); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}><Icon name="chevron" size={15} /></button>{pageNumbers.map(item => <button type="button" aria-label={`第 ${item} 页`} aria-current={item === currentPage ? 'page' : undefined} key={item} onClick={() => { setPage(item); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>{item}</button>)}<button type="button" aria-label="下一页" disabled={currentPage === pages} onClick={() => { setPage(currentPage + 1); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}><Icon name="chevron" size={15} /></button></nav></div>}
      <p className="community-footnote"><Icon name="book" size={13} />面经由社区用户分享，仅供准备参考。请勿发布个人隐私与保密信息。</p>
    </div>

    <aside className="community-aside" aria-label="社区指南"><section className="community-side-card community-profile-card"><div className="community-profile-top"><span className="community-profile-avatar">{username.slice(0, 1).toUpperCase()}</span><div><strong>{username}</strong><span>积累经验，也分享成长</span></div></div><div className="community-personal-stats"><button type="button" onClick={() => { resetFilters(); selectTab('mine') }}><strong>{meta.data?.myPosts ?? '—'}</strong><span>我的面经</span></button><i /><button type="button" onClick={() => { resetFilters(); selectTab('liked') }}><strong>{meta.data?.myLikes ?? '—'}</strong><span>我的点赞</span></button></div><button type="button" className="community-outline-button" disabled={busy} onClick={() => void openEditor()}><Icon name="plus" size={15} />记录一次面试</button></section>
      <section className="community-side-card community-topics"><h2><Icon name="trend" size={18} />热门话题<span>探索</span></h2><p>从你关心的方向开始</p><div>{topics.map(([topic, count], index) => <button type="button" key={topic} onClick={() => { resetFilters(); setTopic(topic); setTab('recommended'); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}><span className={index < 3 ? 'topic-top' : ''}>{String(index + 1).padStart(2, '0')}</span><span>{topic}</span><small>{count} 篇</small><Icon name="chevron" size={12} /></button>)}</div></section>
      <section className="community-side-card community-writing-guide"><span className="community-guide-icon"><Icon name="pencil" size={20} /></span><h2>好面经，不止是题目清单</h2><p>把你的思考过程也分享出来，<br />或许正是另一个人需要的启发。</p><ol><li><span>01</span>聊聊背景：公司、岗位与轮次</li><li><span>02</span>记下问题，以及你的回答思路</li><li><span>03</span>留点复盘：收获与准备建议</li></ol><button type="button" disabled={busy} onClick={() => void openEditor()}>开始记录<Icon name="arrow" size={15} /></button></section>
      <Link className="community-practice-link" to="/interviews/new"><span><Icon name="headphones" size={20} /></span><div><strong>读完面经，练一练</strong><small>把准备变成一次真实的表达</small></div><Icon name="arrow" size={16} /></Link>
      <p className="community-aside-note">彼此启发，一起向前。<br />请勿分享个人隐私和保密信息。</p>
    </aside></div>

    {editorOpen && <Modal title="分享你的面试经历" className="community-editor" busy={busy} onClose={() => void saveDraftAndClose()}><form onSubmit={event => void publish(event)}><fieldset disabled={busy}><div className="community-editor-intro"><h2>把这次经历，写给下一次出发。</h2><p>发布后所有登录用户可见。关闭时，草稿会保存到你的账号。</p></div><label>面经标题<span>必填</span><input data-community-focus required maxLength={100} value={draft.title} onChange={event => changeDraft({ ...draft, title: event.target.value })} placeholder="例如：我的前端二面复盘，有收获也有遗憾" /></label><div className="community-form-grid"><label>面试公司<input required maxLength={50} value={draft.company} onChange={event => changeDraft({ ...draft, company: event.target.value })} placeholder="例如：字节跳动" /></label><label>应聘岗位<input required maxLength={60} value={draft.role} onChange={event => changeDraft({ ...draft, role: event.target.value })} placeholder="例如：前端工程师" /></label></div><div className="community-form-grid community-form-thirds"><label>岗位方向<Select aria-label="岗位方向" value={draft.direction} onValueChange={value => changeDraft({ ...draft, direction: value as Direction })} options={directions.map(item => ({ value: item, label: item }))} /></label><label>面试阶段<Select aria-label="面试阶段" value={draft.stage} onValueChange={stage => changeDraft({ ...draft, stage })} options={['校招 · 一面', '校招 · 二面', '校招 · 三面', '实习 · 一面', '实习 · 二面', '社招 · 一面', '社招 · 二面', '社招 · 终面'].map(item => ({ value: item, label: item }))} /></label><label>面试进展<Select aria-label="面试进展" value={draft.outcome} onValueChange={value => changeDraft({ ...draft, outcome: value as Outcome })} options={outcomes.map(item => ({ value: item, label: item }))} /></label></div><label>话题标签<span>选填，最多 5 个</span><input maxLength={100} value={draft.tags} onChange={event => changeDraft({ ...draft, tags: event.target.value })} placeholder="用逗号或空格分隔，例如 React、项目深挖、校招" /></label><div className="community-content-label"><label htmlFor="community-post-content">面经正文<span>至少 20 字</span></label><button type="button" onClick={() => setPreview(value => !value)}>{preview ? '继续编辑' : '预览排版'}</button></div>{preview ? <div className="community-markdown-preview"><RichText text={draft.content || '还没有内容，先写下你的第一句话吧。'} /></div> : <textarea id="community-post-content" required minLength={20} maxLength={20000} rows={8} value={draft.content} onChange={event => changeDraft({ ...draft, content: event.target.value })} placeholder={'## 面试背景\n介绍一下岗位、轮次和准备情况…\n\n## 面试问题\n有哪些问题让你印象深刻？\n\n## 我的复盘\n有哪些收获，下次会如何准备？'} />}<div className="community-editor-hint"><span>支持 Markdown · 请避免个人隐私和保密内容</span><span>{draft.content.length.toLocaleString()} / 20,000</span></div>{error && <Notice>{error}</Notice>}<footer className="community-editor-footer"><span><Icon name="file" size={14} />草稿仅自己可见</span><div><button type="button" className="btn" onClick={() => void saveDraftAndClose()}>暂存关闭</button><button type="submit" className="btn primary"><Icon name="check" size={16} />{busy ? '正在保存…' : '发布面经'}</button></div></footer></fieldset></form></Modal>}

    {postId && !editorOpen && <Modal title="面经详情" className="community-reader" busy={busy} onClose={closePost}>
      {!activePost && detail.loading && <Loading text="正在加载面经全文…" />}
      {detail.error && <Notice>{detail.error}<button type="button" className="text-button" onClick={detail.refresh}>重试</button></Notice>}
      {activePost && <div className="community-reader-body">
        <div className="community-reader-kicker"><span>{activePost.company} / {activePost.role}</span><span className="community-outcome">{activePost.stage}</span></div>
        <h1>{activePost.title}</h1><div className="community-reader-author"><span className="community-avatar avatar-sage">{activePost.author.slice(0, 1)}</span><strong>{activePost.author}</strong><time dateTime={activePost.createdAt}>{new Date(activePost.createdAt).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}</time></div>
        <div className="community-post-tags">{activePost.tags.map(tag => <span key={tag}>{tag}</span>)}</div><RichText text={activePost.content ?? ''} />
        <div className="community-reader-actions"><button type="button" disabled={pendingLikes.includes(activePost.id)} className={`btn${activePost.liked ? ' community-action-selected' : ''}`} aria-pressed={activePost.liked} onClick={() => void toggleLiked(activePost)}><Icon name="like" size={16} />{activePost.liked ? '已点赞' : '点赞'} · {activePost.likes}</button></div>
        <section className="community-comments"><h2>交流与补充<span>{activePost.commentCount}</span></h2><p>交流准备思路，也欢迎补充你的面试经验。</p>
          {comments.loading && !comments.data && <Loading text="正在加载评论…" />}
          {comments.error && <Notice>{comments.error}<button type="button" className="text-button" onClick={comments.refresh}>重试</button></Notice>}
          {(comments.data?.items ?? []).map(item => <article key={item.id}><span className="community-avatar avatar-sage">{item.author.slice(0, 1)}</span><div><strong>{item.author}<time dateTime={item.createdAt}>{dateLabel(item.createdAt)}</time></strong><p>{item.content}</p></div></article>)}
          {comments.data && (commentPage > 1 || comments.data.hasMore) && <nav className="community-comment-pagination" aria-label="评论分页"><button type="button" className="text-button" disabled={commentPage === 1} onClick={() => setCommentPage(value => value - 1)}>上一页评论</button><span>第 {commentPage} 页</span><button type="button" className="text-button" disabled={!comments.data.hasMore} onClick={() => setCommentPage(value => value + 1)}>下一页评论</button></nav>}
          <form onSubmit={event => void addComment(event)}><label htmlFor="community-comment" className="sr-only">评论内容</label><textarea id="community-comment" maxLength={2000} rows={3} required disabled={busy} value={comment} onChange={event => setComment(event.target.value)} placeholder="有哪些值得补充的面试经验？" /><div><span>{comment.length} / 2,000</span><button type="submit" className="btn primary" disabled={busy || !comment.trim()}>{busy ? '正在发布…' : '发布评论'}<Icon name="arrow" size={14} /></button></div></form>
        </section>{error && <Notice>{error}</Notice>}
      </div>}
    </Modal>}
  </div>;
}
