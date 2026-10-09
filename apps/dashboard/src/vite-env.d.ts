/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** 작업자 화면 주소(클라우드 배포처럼 다른 도메인일 때만). */
  readonly VITE_HMI_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
