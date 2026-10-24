export const directions = ['前端开发', '后端开发', '算法 / AI', '产品 / 设计', '测试开发'] as const;
export const outcomes = ['已拿 Offer', '进入下一轮', '持续复盘'] as const;
export type Direction = typeof directions[number];
export type Outcome = typeof outcomes[number];
export interface CommunityPost {
  id: string; authorId: string; title: string; company: string; role: string;
  direction: Direction; stage: string; outcome: Outcome; tags: string[];
  summary: string; content?: string; author: string; createdAt: string;
  likes: number; liked: boolean; commentCount: number; featured: boolean;
}
export interface CommunityComment { id: string; author: string; content: string; createdAt: string }
export interface CommunityFeed { items: CommunityPost[]; total: number; page: number; pageSize: number }
export interface CommunityMeta { companies: string[]; topics: { tag: string; count: number }[]; myPosts: number; myLikes: number }
export interface CommentPage { items: CommunityComment[]; total: number; page: number; hasMore: boolean }
export interface CommunityDraft {
  id: string; title: string; company: string; role: string; direction: Direction;
  stage: string; outcome: Outcome; tags: string; content: string;
}
export interface DraftResult { draft: CommunityDraft | null; version: number }
