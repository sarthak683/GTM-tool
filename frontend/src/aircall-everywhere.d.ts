declare module "aircall-everywhere" {
  interface AircallPhoneOptions {
    domToLoadWorkspace?: string;
    onLogin?: (payload: { user: unknown }) => void;
    onLogout?: () => void;
    size?: "big" | "small";
    debug?: boolean;
    [key: string]: unknown;
  }

  export default class AircallPhone {
    constructor(options?: AircallPhoneOptions);
    send(event: string, data?: Record<string, unknown>, callback?: (success: boolean) => void): void;
    on(event: string, callback: (data?: unknown) => void): void;
  }
}
