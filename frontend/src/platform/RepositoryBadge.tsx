import { Icon } from './ui';

const repository = {
  name: 'Resume-Agent',
  url: 'https://github.com/Katrina55553/Resume-Agent',
  stars: 4,
  forks: 0,
};

export function RepositoryBadge() {
  return <a
    className="repository-badge"
    href={repository.url}
    target="_blank"
    rel="noopener noreferrer"
    aria-label={`${repository.name} GitHub 仓库，${repository.stars} 个 star，${repository.forks} 个 fork（在新窗口打开）`}
    title="在 GitHub 查看项目"
  >
    <svg className="repository-badge-logo" width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2C6.477 2 2 6.477 2 12c0 4.419 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.009-.868-.014-1.703-2.782.604-3.369-1.342-3.369-1.342-.455-1.156-1.11-1.464-1.11-1.464-.908-.62.069-.608.069-.608 1.003.071 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.091-.646.349-1.087.635-1.337-2.221-.253-4.555-1.111-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.564 9.564 0 0 1 12 6.829a9.59 9.59 0 0 1 2.504.337c1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.202 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.841-2.337 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.337-.012 2.416-.012 2.744 0 .267.18.577.688.48A10.001 10.001 0 0 0 22 12c0-5.523-4.477-10-10-10Z" />
    </svg>
    <span className="repository-badge-content">
      <span className="repository-badge-name">{repository.name}</span>
      <span className="repository-badge-stats">
        <span><Icon name="star" size={14} />{repository.stars}</span>
        <span><Icon name="fork" size={14} />{repository.forks}</span>
      </span>
    </span>
  </a>;
}
