/** 테스트용 WebSocket 목 — 수동으로 open/message/close 를 트리거한다. */
export class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CLOSED = 3;

  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  // 인스턴스에도 상수 노출(코드가 WebSocket.OPEN 으로 접근).
  OPEN = 1;
  CLOSED = 3;

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }

  // --- 테스트 트리거 ---
  triggerOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  triggerMessage(payload: unknown) {
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }

  /**
   * 서버측 종료를 시뮬레이트한다. code 미지정 시 정상 종료(재연결 대상),
   * code=1008 등 지정 시 정책 위반(인증 실패) 종료를 흉내낸다.
   */
  triggerServerClose(code?: number) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", code != null ? { code } : undefined));
  }

  static last(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  static reset() {
    MockWebSocket.instances = [];
  }
}
