import { HttpApiClient, type ApiClient } from "./client";

export * from "./client";
export type * from "./types";

/** True when the app was started with VITE_USE_MOCK=1. */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

/** The active client. The real HTTP client is the default; call initApi() before rendering. */
export let api: ApiClient = new HttpApiClient();

export async function initApi(): Promise<void> {
  if (USE_MOCK) {
    const { MockApiClient } = await import("./mock");
    api = new MockApiClient();
  }
}
