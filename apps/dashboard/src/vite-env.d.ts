/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** 작업자 화면 주소(클라우드 배포처럼 다른 도메인일 때만). */
  readonly VITE_HMI_URL?: string;
  /** MES(services/mes) 주소. 비우면 80/443 은 /mes-api, 그 외는 같은 호스트 :8100. */
  readonly VITE_MES_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
