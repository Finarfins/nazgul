/// <reference types="vite/client" />

interface Window {
  __FIELD_E2E__?: {
    applySnapshot: typeof import('./field/fieldDatabase').applySnapshot;
    captureWriteEpoch: typeof import('./field/fieldDatabase').captureWriteEpoch;
  };
}
