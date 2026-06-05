// API base URL from Vite env (VITE_API_BASE). Empty -> same-origin (nginx proxy).
export const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
