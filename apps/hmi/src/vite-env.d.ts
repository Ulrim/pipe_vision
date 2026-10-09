/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_URL?: string;
  /** 이 화면이 고정으로 보는 스테이션(카메라) — 2대 구성. 비우면 전부. */
  readonly VITE_CAM_ID?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
