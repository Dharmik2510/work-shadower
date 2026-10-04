import type {
  AvatarKind,
  CreateRunRequest,
  CreateSkillRequest,
  DevLoginRequest,
  DevLoginResponse,
  FilterStats,
  Flags,
  ID,
  Job,
  ListSkillsParams,
  Page,
  PageParams,
  PatchSkillRequest,
  PublicConfig,
  Recording,
  Run,
  RunStepReport,
  SearchHit,
  Skill,
  SkillSummary,
  SkillVersion,
  SkillVersionMeta,
  SuggestFixRequest,
  TeamRef,
  Usage,
  User,
} from "./types";
import { getToken } from "../auth/session";

export const API_BASE = "/api/v1";

/** Error thrown for any non-2xx response, carrying the contract's error envelope. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Client-generated UUID for the Idempotency-Key header. */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** The surface the UI depends on. Implemented by HttpApiClient and MockApiClient. */
export interface ApiClient {
  // auth & config
  publicConfig(): Promise<PublicConfig>;
  devLogin(body: DevLoginRequest): Promise<DevLoginResponse>;
  me(): Promise<User>;
  updateMe(body: { avatar: AvatarKind }): Promise<User>;
  teams(): Promise<{ items: TeamRef[] }>;
  config(): Promise<Flags>;
  // assets
  assetBlob(sha256: string): Promise<Blob>;
  // recordings
  listRecordings(params?: PageParams): Promise<Page<Recording>>;
  getRecording(id: ID): Promise<Recording>;
  // skills
  listSkills(params?: ListSkillsParams): Promise<Page<SkillSummary>>;
  search(q: string, limit?: number): Promise<{ items: SearchHit[] }>;
  createSkill(body: CreateSkillRequest): Promise<Skill>;
  getSkill(id: ID): Promise<Skill>;
  patchSkill(id: ID, body: PatchSkillRequest): Promise<Skill>;
  publishSkill(id: ID): Promise<Skill>;
  archiveSkill(id: ID): Promise<Skill>;
  listVersions(id: ID): Promise<{ items: SkillVersionMeta[] }>;
  getVersion(id: ID, n: number): Promise<SkillVersion>;
  suggestFix(id: ID, body: SuggestFixRequest): Promise<unknown>;
  // runs
  createRun(body: CreateRunRequest, idempotencyKey?: string): Promise<{ id: ID }>;
  reportRunStep(runId: ID, body: RunStepReport): Promise<unknown>;
  listRuns(skillId: ID, params?: PageParams): Promise<Page<Run>>;
  // admin
  getAdminFlags(): Promise<Flags>;
  putAdminFlags(flags: Flags): Promise<Flags>;
  usage(days?: number): Promise<Usage>;
  filterStats(days?: number): Promise<FilterStats>;
  filterExport(days?: number): Promise<Blob>;
  deadJobs(): Promise<{ items: Job[] }>;
  retryJob(id: ID): Promise<unknown>;
}

type Query = Record<string, string | number | boolean | undefined | null>;

function qs(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Fired when the server rejects our token; the auth layer listens and signs out. */
export const UNAUTHORIZED_EVENT = "ws:unauthorized";

export class HttpApiClient implements ApiClient {
  constructor(private base = API_BASE) {}

  private async raw(
    method: string,
    path: string,
    opts: { body?: unknown; query?: Query; headers?: Record<string, string>; auth?: boolean } = {},
  ): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.auth !== false) {
      const t = getToken();
      if (t) headers.Authorization = `Bearer ${t}`;
    }
    let res: Response;
    try {
      res = await fetch(this.base + path + qs(opts.query), {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch {
      throw new ApiError(0, "network_error", "Can't reach the Work Shadower server. Check your connection and try again.");
    }
    if (!res.ok) {
      let code = `http_${res.status}`;
      let message = res.statusText || "Request failed";
      try {
        const j = await res.json();
        if (j?.error) {
          code = j.error.code ?? code;
          message = j.error.message ?? message;
        } else if (typeof j?.detail === "string") {
          message = j.detail;
        }
      } catch {
        /* non-JSON error body */
      }
      if (res.status === 401 && opts.auth !== false) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      throw new ApiError(res.status, code, message);
    }
    return res;
  }

  private async json<T>(method: string, path: string, opts: Parameters<HttpApiClient["raw"]>[2] = {}): Promise<T> {
    const res = await this.raw(method, path, opts);
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  publicConfig() {
    return this.json<PublicConfig>("GET", "/config/public", { auth: false });
  }
  devLogin(body: DevLoginRequest) {
    return this.json<DevLoginResponse>("POST", "/auth/dev-login", { body, auth: false });
  }
  me() {
    return this.json<User>("GET", "/me");
  }
  updateMe(body: { avatar: AvatarKind }) {
    return this.json<User>("PATCH", "/me", { body });
  }
  teams() {
    return this.json<{ items: TeamRef[] }>("GET", "/teams");
  }
  config() {
    return this.json<Flags>("GET", "/config");
  }

  async assetBlob(sha256: string) {
    // fetch follows the 302 to the presigned URL; browsers drop the Authorization
    // header on cross-origin redirects, which is what presigned URLs expect.
    const res = await this.raw("GET", `/assets/${encodeURIComponent(sha256)}`, { headers: { Accept: "image/*" } });
    return res.blob();
  }

  listRecordings(params?: PageParams) {
    return this.json<Page<Recording>>("GET", "/recordings", { query: { ...params } });
  }
  getRecording(id: ID) {
    return this.json<Recording>("GET", `/recordings/${id}`);
  }

  listSkills(params: ListSkillsParams = {}) {
    return this.json<Page<SkillSummary>>("GET", "/skills", { query: { ...params } });
  }
  search(q: string, limit = 20) {
    return this.json<{ items: SearchHit[] }>("GET", "/search", { query: { q, limit } });
  }
  createSkill(body: CreateSkillRequest) {
    return this.json<Skill>("POST", "/skills", { body });
  }
  getSkill(id: ID) {
    return this.json<Skill>("GET", `/skills/${id}`);
  }
  patchSkill(id: ID, body: PatchSkillRequest) {
    return this.json<Skill>("PATCH", `/skills/${id}`, { body });
  }
  publishSkill(id: ID) {
    return this.json<Skill>("POST", `/skills/${id}/publish`);
  }
  archiveSkill(id: ID) {
    return this.json<Skill>("POST", `/skills/${id}/archive`);
  }
  listVersions(id: ID) {
    return this.json<{ items: SkillVersionMeta[] }>("GET", `/skills/${id}/versions`);
  }
  getVersion(id: ID, n: number) {
    return this.json<SkillVersion>("GET", `/skills/${id}/versions/${n}`);
  }
  suggestFix(id: ID, body: SuggestFixRequest) {
    return this.json<unknown>("POST", `/skills/${id}/suggest-fix`, { body });
  }

  createRun(body: CreateRunRequest, idempotencyKey = newIdempotencyKey()) {
    return this.json<{ id: ID }>("POST", "/runs", { body, headers: { "Idempotency-Key": idempotencyKey } });
  }
  reportRunStep(runId: ID, body: RunStepReport) {
    return this.json<unknown>("POST", `/runs/${runId}/steps`, { body });
  }
  listRuns(skillId: ID, params?: PageParams) {
    return this.json<Page<Run>>("GET", "/runs", { query: { skill_id: skillId, ...params } });
  }

  getAdminFlags() {
    return this.json<Flags>("GET", "/admin/flags");
  }
  putAdminFlags(flags: Flags) {
    return this.json<Flags>("PUT", "/admin/flags", { body: flags });
  }
  usage(days = 30) {
    return this.json<Usage>("GET", "/admin/usage", { query: { days } });
  }
  filterStats(days = 30) {
    return this.json<FilterStats>("GET", "/admin/filter/stats", { query: { days } });
  }
  async filterExport(days = 90) {
    const res = await this.raw("GET", `/admin/filter/export?days=${days}`, { headers: { Accept: "application/x-ndjson" } });
    return res.blob();
  }
  async deadJobs() {
    // Contract doesn't say whether this is paged or a bare array; accept both.
    const r = await this.json<{ items: Job[] } | Job[]>("GET", "/admin/jobs", { query: { status: "dead" } });
    return Array.isArray(r) ? { items: r } : r;
  }
  retryJob(id: ID) {
    return this.json<unknown>("POST", `/admin/jobs/${id}/retry`);
  }
}
