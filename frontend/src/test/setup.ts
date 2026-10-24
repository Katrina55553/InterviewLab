/**
 * Vitest 全局测试初始化
 */
import { vi } from 'vitest'

// Radix scrolls the highlighted option into view; jsdom has no layout engine.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

// jsdom 不实现 WebSocket，提供 mock
class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  url: string
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    // 记录最后创建的实例，供测试访问
    ;(MockWebSocket as unknown as { _lastInstance: WebSocket })._lastInstance = this as unknown as WebSocket
    // 异步触发 onopen，模拟连接成功
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN
      this.onopen?.(new Event('open'))
    }, 0)
  }

  send(data: string): void {
    // 测试中可 spy 此方法
    void data
  }

  close(code = 1000, reason?: string): void {
    void reason
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close', { code }))
  }
}

// 注入到 globalThis
globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket

// jsdom 实现了 location，但 host 可能为空，确保有默认值
if (!globalThis.location.host) {
  Object.defineProperty(globalThis, 'location', {
    value: {
      protocol: 'http:',
      host: 'localhost:5173',
      href: 'http://localhost:5173/',
    },
    writable: true,
  })
}

// 静默 console.log（保留 error/warn）
vi.spyOn(console, 'log').mockImplementation(() => {})
