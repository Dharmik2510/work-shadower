// Types mirroring docs/CONTRACT.md (v1). Keep in sync with the contract.
// Shapes the contract leaves open are marked "ASSUMED" — see web/README.md.

export type ID = string;
export type ISODate = string;

// ---------- Errors / paging ----------
export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface PageParams {
  limit?: number;
  cursor?: string;
}

// ---------- Auth & config ----------
export type Role = "member" | "admin";

export interface TeamRef {
  id: ID;
  name: string;
}

export interface User {
  id: ID;
  email: string;
  name: string;
  role: Role;
  teams: TeamRef[];
}

export interface UserRef {
  id: ID;
  name: string;
  email: string;
}

export interface DevLoginRequest {
  email: string;
  name: string;
  team?: string;
}

export interface DevLoginResponse {
  token: string;
  user: User;
}

export type AuthMode = "dev" | "oidc";

export interface PublicConfig {
  auth_mode: AuthMode;
  oidc: { issuer: string; client_id: string } | null;
}

export type ScreenshotPolicy = "key_moments" | "none";

export interface Flags {
  recording_enabled: boolean;
  replay_enabled: boolean;
  llm_enabled: boolean;
  max_recording_minutes: number;
  screenshot_policy: ScreenshotPolicy;
}

// ---------- Recorded events ----------
export type EventType =
  | "app_activate"
  | "click"
  | "type"
  | "key"
  | "menu"
  | "window_open"
  | "url_change"
  | "scroll";

export type ValueKind = "text" | "secure" | "none";

export interface RecordedEvent {
  seq: number;
  ts: ISODate;
  type: EventType;
  app: { bundle_id: string; name: string };
  window?: { title: string };
  element?: {
    role: string;
    subrole: string | null;
    label: string | null;
    identifier: string | null;
    path: string[];
    value_kind: ValueKind;
  };
  text?: string;
  key?: string;
  url?: string;
  screenshot_sha256?: string | null;
}

// ---------- Skill content ----------
export type ActionType = "open_app" | "open_url" | "click" | "type" | "key" | "menu" | "wait";

export interface ElementTarget {
  role: string;
  label: string | null;
  identifier: string | null;
  path: string[];
  window_title?: string | null;
}

export interface StepAction {
  type: ActionType;
  target?: ElementTarget | null;
  text?: string | null;
  key?: string | null;
  url?: string | null;
}

export interface StepExpect {
  window_title_contains?: string | null;
  element_present?: { role: string; label: string | null } | null;
}

export interface SkillStep {
  index: number;
  title: string;
  instruction: string;
  app: string;
  action: StepAction;
  expect?: StepExpect | null;
  screenshot_sha256: string | null;
  irreversible: boolean;
}

export interface SkillInput {
  name: string;
  description: string;
  example: string;
}

export interface SkillContent {
  title: string;
  goal: string;
  apps: string[];
  prerequisites: string[];
  inputs: SkillInput[];
  steps: SkillStep[];
  tags: string[];
}

// ---------- Skills ----------
export type Visibility = "private" | "team" | "org";
export type SkillStatus = "draft" | "published" | "archived";

export interface SkillHealth {
  runs: number;
  success_rate: number | null; // null when no runs (ASSUMED nullable)
  last_run_at: ISODate | null;
}

export interface Skill {
  id: ID;
  owner: UserRef;
  team: TeamRef | null;
  visibility: Visibility;
  status: SkillStatus;
  current_version: number; // 0 when never published (ASSUMED)
  draft: SkillContent | null;
  published: SkillContent | null;
  source_recording_id: ID | null;
  created_at: ISODate;
  updated_at: ISODate;
  health: SkillHealth;
}

export interface SkillSummary {
  id: ID;
  title: string;
  goal: string;
  owner: UserRef;
  team: TeamRef | null;
  visibility: Visibility;
  status: SkillStatus;
  tags: string[];
  apps: string[];
  current_version: number;
  updated_at: ISODate;
  health: SkillHealth;
}

export interface SearchHit extends SkillSummary {
  score: number;
}

export interface ListSkillsParams extends PageParams {
  q?: string;
  team_id?: ID;
  status?: SkillStatus;
  mine?: boolean;
}

export interface CreateSkillRequest {
  content: SkillContent;
  team_id?: ID | null;
  visibility: Visibility;
}

export interface PatchSkillRequest {
  content?: SkillContent;
  team_id?: ID | null;
  visibility?: Visibility;
}

/** ASSUMED: created_by may be a user ref or a plain id/name string. */
export type CreatedBy = UserRef | { id: ID; name: string } | string;

export interface SkillVersionMeta {
  version: number;
  created_at: ISODate;
  created_by: CreatedBy;
}

export interface SkillVersion extends SkillVersionMeta {
  content: SkillContent;
}

export interface SuggestFixRequest {
  step_index: number;
  new_target: ElementTarget;
  note: string;
}

// ---------- Recordings ----------
export type RecordingStatus = "received" | "processing" | "ready" | "failed";

export interface Recording {
  id: ID;
  status: RecordingStatus;
  error?: string | null;
  skill_id?: ID | null;
  event_count: number;
  created_at: ISODate;
  /** ASSUMED optional extra; shown if present. */
  title_hint?: string | null;
}

export interface CreateRecordingRequest {
  title_hint?: string;
  started_at: ISODate;
  ended_at: ISODate;
  client: { app_version: string; os_version: string; device_id: string };
  events: RecordedEvent[];
}

// ---------- Runs ----------
export type RunMode = "guided" | "auto";
export type RunStatus = "running" | "succeeded" | "failed" | "aborted"; // "running" ASSUMED for unfinished runs
export type StepStatus = "ok" | "repaired" | "failed" | "skipped" | "confirmed";
export type StepStrategy = "deterministic" | "llm_repair" | "vision" | "human";

export interface CreateRunRequest {
  skill_id: ID;
  version: number;
  mode: RunMode;
  inputs: Record<string, string>;
}

export interface RunStepReport {
  step_index: number;
  status: StepStatus;
  strategy: StepStrategy;
  duration_ms: number;
  detail?: string | null;
}

/** ASSUMED run list item shape (contract only says "paged"). */
export interface Run {
  id: ID;
  skill_id: ID;
  version: number;
  mode: RunMode;
  status: RunStatus;
  user?: UserRef | null;
  started_at: ISODate;
  finished_at?: ISODate | null;
  error?: string | null;
  steps?: RunStepReport[];
}

// ---------- Admin ----------
/** ASSUMED by_team item shape. */
export interface TeamUsage {
  team_id?: ID | null;
  team?: string | TeamRef | null;
  team_name?: string | null;
  llm_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  est_cost_usd?: number;
}

export interface Usage {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  est_cost_usd: number;
  by_team: TeamUsage[];
  skills_created: number;
  runs: number;
  run_success_rate: number | null;
}

/** ASSUMED dead job shape. */
export interface Job {
  id: ID;
  kind?: string;
  type?: string;
  status: string;
  attempts?: number;
  max_attempts?: number;
  last_error?: string | null;
  error?: string | null;
  created_at?: ISODate;
  updated_at?: ISODate;
  payload?: Record<string, unknown>;
}

export interface Healthz {
  ok: boolean;
  db: boolean;
  storage: boolean;
}
