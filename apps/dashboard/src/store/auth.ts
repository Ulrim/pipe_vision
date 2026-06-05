import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Role } from "@aivis/shared-types";

/**
 * Minimal token store (§7 7) — persists JWT after POST /auth/login.
 * Used by the api client to attach the Authorization header.
 */
export interface AuthState {
  token: string | null;
  username: string | null;
  role: Role | null;
  setAuth: (p: { token: string; username: string; role: Role }) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      username: null,
      role: null,
      setAuth: ({ token, username, role }) => set({ token, username, role }),
      clear: () => set({ token: null, username: null, role: null }),
    }),
    { name: "aivis.dashboard.auth" },
  ),
);

/** Non-react access for the api client (avoids hook coupling). */
export function getToken(): string | null {
  return useAuthStore.getState().token;
}

/** quality+ may edit master/items, KPI manual, reports (API.md). */
export function canEdit(role: Role | null): boolean {
  return role === "quality" || role === "admin";
}
