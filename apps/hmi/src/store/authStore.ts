/**
 * 경량 인증 상태 (CLAUDE.md §5 M10, §7.4 /auth/login).
 *
 * 현장 단말 특성: "한 번 로그인 후 유지" UX → 토큰/역할을 localStorage 에 영속화.
 * 재확인(PATCH /inspection/{id}/review) 같은 쓰기 액션만 인증을 요구하고,
 * 실시간 표시(WS /ws/live)는 미인증이어도 동작한다.
 *
 * 도메인 타입(Role/TokenResponse)은 @aivis/shared-types 재사용(신규 정의 금지).
 */
import { create } from "zustand";
import type { Role, TokenResponse } from "@aivis/shared-types";

const STORAGE_KEY = "aivis.hmi.auth";

export interface AuthSession {
  token: string;
  role: Role;
  username: string;
}

interface PersistedAuth {
  token: string;
  role: Role;
  username: string;
}

function loadSession(): AuthSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw) as Partial<PersistedAuth>;
    if (!obj.token || !obj.role || !obj.username) return null;
    return { token: obj.token, role: obj.role, username: obj.username };
  } catch {
    return null;
  }
}

function persist(session: AuthSession | null): void {
  try {
    const ls = globalThis.localStorage;
    if (!ls) return;
    if (session) {
      ls.setItem(STORAGE_KEY, JSON.stringify(session));
    } else {
      ls.removeItem(STORAGE_KEY);
    }
  } catch {
    /* localStorage 미가용(프라이빗 모드 등) → 메모리 상태만 유지 */
  }
}

export interface AuthState {
  /** 현재 세션(미인증이면 null). */
  session: AuthSession | null;
  /** 로그인 모달 표시 여부(쓰기 액션이 401/미인증으로 유도). */
  loginPromptOpen: boolean;

  /** 인증 여부. */
  isAuthenticated: () => boolean;
  /** Bearer 토큰(없으면 null). */
  token: () => string | null;
  /** 로그인 성공 시 토큰 응답을 세션으로 저장. */
  setSession: (token: TokenResponse) => void;
  /** 로그아웃 — 세션 폐기. */
  logout: () => void;
  /** 로그인 모달 열기(쓰기 액션이 인증 필요할 때). */
  openLoginPrompt: () => void;
  /** 로그인 모달 닫기. */
  closeLoginPrompt: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  session: loadSession(),
  loginPromptOpen: false,

  isAuthenticated: () => get().session !== null,
  token: () => get().session?.token ?? null,

  setSession: (tok) => {
    const session: AuthSession = {
      token: tok.access_token,
      role: tok.role,
      username: tok.username,
    };
    persist(session);
    set({ session, loginPromptOpen: false });
  },

  logout: () => {
    persist(null);
    set({ session: null });
  },

  openLoginPrompt: () => set({ loginPromptOpen: true }),
  closeLoginPrompt: () => set({ loginPromptOpen: false }),
}));

/** 비-React 컨텍스트(api client)에서 현재 토큰 접근용. */
export function getAuthToken(): string | null {
  return useAuthStore.getState().token();
}
