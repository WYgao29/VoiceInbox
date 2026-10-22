/// <reference types="vite/client" />

interface DesktopBackend {
  port: number;
  owned: boolean;
  reason: string;
  python?: string;
  pythonSource?: "" | "launcher" | "venv";
  pythonExecutable?: boolean;
}

interface VoiceinboxDesktop {
  isDesktop: boolean;
  backend: () => Promise<DesktopBackend>;
  openExternal: (url: string) => Promise<void>;
}

interface ShotSpec {
  theme: string;
  view: string;
  tab?: string;
  empty?: boolean;
  dialog?: boolean;
}

interface Window {
  voiceinboxDesktop?: VoiceinboxDesktop;
  __voiceinboxShot?: (spec: ShotSpec) => Promise<boolean>;
}
