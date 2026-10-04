// In-memory mock of the Work Shadower API (enabled with VITE_USE_MOCK=1).
// Mirrors ApiClient exactly so screens can be built and verified without a backend.
import { ApiError, type ApiClient } from "./client";
import type {
  AvatarKind,
  CreateRunRequest,
  CreateSkillRequest,
  DevLoginRequest,
  FilterStats,
  Flags,
  ID,
  Job,
  ListSkillsParams,
  PatchSkillRequest,
  Recording,
  Run,
  RunStepReport,
  SearchHit,
  Skill,
  SkillContent,
  SkillStep,
  SkillSummary,
  SkillVersion,
  TeamRef,
  Usage,
  User,
  UserRef,
} from "./types";
import { getToken } from "../auth/session";

const wait = (ms = 120 + Math.random() * 180) => new Promise((r) => setTimeout(r, ms));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));
const now = Date.now();
const ago = (mins: number) => new Date(now - mins * 60_000).toISOString();
let seq = 1000;
const uid = (p = "0000") => `${p.padStart(8, "0")}-0000-4000-8000-${String(++seq).padStart(12, "0")}`;

// ---------- teams & people ----------
const T = {
  claims: { id: "t-claims-0001", name: "Auto Claims" },
  pl: { id: "t-pl-0002", name: "Personal Lines UW" },
  broker: { id: "t-broker-0003", name: "Broker Support" },
  ubi: { id: "t-ubi-0004", name: "UBI" },
} satisfies Record<string, TeamRef>;

const P = {
  priya: { id: "u-priya", name: "Priya Raman", email: "priya.raman@example.com", avatar: "sprout" },
  marc: { id: "u-marc", name: "Marc Tremblay", email: "marc.tremblay@example.com", avatar: "ember" },
  aisha: { id: "u-aisha", name: "Aisha Okafor", email: "aisha.okafor@example.com", avatar: "nimbus" },
} satisfies Record<string, UserRef>;

let me: User = {
  id: "u-me",
  email: "dharmik@example.com",
  name: "Dharmik",
  role: "admin",
  teams: [T.ubi, T.claims],
  avatar: "orb",
};
const meRef = (): UserRef => ({ id: me.id, name: me.name, email: me.email, avatar: me.avatar });

// ---------- screenshots (generated SVG "app windows") ----------
interface Shot {
  app: string;
  window: string;
  url?: string;
  fields: [string, string][];
  highlight: string;
  danger?: boolean;
}
const shots = new Map<string, Shot>();
let shotSeq = 0;
function shot(s: Shot): string {
  const hex = (++shotSeq).toString(16).padStart(4, "0");
  const sha = `${"a3f9c2e17b".repeat(6)}${hex}`.slice(0, 64);
  shots.set(sha, s);
  return sha;
}

function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function renderShot(s: Shot): string {
  const W = 960,
    H = 600;
  const rows = s.fields
    .map(
      ([label, value], i) => `
      <text x="64" y="${196 + i * 62}" font-size="15" fill="#5b6075">${esc(label)}</text>
      <rect x="64" y="${206 + i * 62}" width="420" height="34" rx="5" fill="#fff" stroke="#cfd3df"/>
      <text x="76" y="${228 + i * 62}" font-size="15" fill="#1d2033">${esc(value)}</text>`,
    )
    .join("");
  const btnY = 206 + s.fields.length * 62 + 14;
  const color = s.danger ? "#c2412d" : "#2f6fde";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Helvetica, Arial, sans-serif">
  <rect width="${W}" height="${H}" fill="#eef0f5"/>
  <rect x="0" y="0" width="${W}" height="44" fill="#e2e5ec"/>
  <circle cx="22" cy="22" r="6" fill="#ff5f57"/><circle cx="42" cy="22" r="6" fill="#febc2e"/><circle cx="62" cy="22" r="6" fill="#28c840"/>
  <rect x="200" y="11" width="560" height="22" rx="6" fill="#fff"/>
  <text x="214" y="27" font-size="13" fill="#6b7085">${esc(s.url ?? s.app)}</text>
  <rect x="0" y="44" width="${W}" height="52" fill="#1f3354"/>
  <text x="28" y="77" font-size="18" font-weight="700" fill="#fff">${esc(s.window)}</text>
  <rect x="0" y="96" width="210" height="${H - 96}" fill="#f7f8fb"/>
  ${["Summary", "Details", "Parties", "Documents", "Notes"].map((t, i) => `<text x="28" y="${136 + i * 34}" font-size="14" fill="${i === 1 ? "#1f3354" : "#7a8096"}" font-weight="${i === 1 ? 700 : 400}">${t}</text>`).join("")}
  <g transform="translate(210,0)">
    <text x="64" y="150" font-size="20" font-weight="700" fill="#1d2033">${esc(s.window)}</text>
    ${rows}
    <rect x="${64 - 6}" y="${btnY - 6}" width="${s.highlight.length * 9 + 52}" height="52" rx="10" fill="none" stroke="#f5a524" stroke-width="3"/>
    <rect x="64" y="${btnY}" width="${s.highlight.length * 9 + 40}" height="40" rx="6" fill="${color}"/>
    <text x="84" y="${btnY + 26}" font-size="15" font-weight="700" fill="#fff">${esc(s.highlight)}</text>
  </g>
</svg>`;
}

// ---------- seed skills ----------
function step(
  index: number,
  title: string,
  instruction: string,
  app: string,
  action: SkillStep["action"],
  opts: { shot?: Shot; irreversible?: boolean; expect?: SkillStep["expect"] } = {},
): SkillStep {
  return {
    index,
    title,
    instruction,
    app,
    action,
    expect: opts.expect ?? null,
    screenshot_sha256: opts.shot ? shot(opts.shot) : null,
    irreversible: !!opts.irreversible,
  };
}
const btn = (label: string, window_title: string) => ({ role: "AXButton", label, identifier: null, path: [], window_title });
const field = (label: string, window_title: string) => ({ role: "AXTextField", label, identifier: null, path: [], window_title });

const fnol: SkillContent = {
  title: "File a new auto claim (first notice of loss)",
  goal: "Open a new auto claim in ClaimCenter from a customer's phone report, attach the loss details, and assign it to the right adjuster queue.",
  apps: ["Google Chrome", "Guidewire ClaimCenter"],
  prerequisites: ["ClaimCenter access with the Claims Intake role", "Customer's policy number and date of loss"],
  inputs: [
    { name: "policy_number", description: "Auto policy the loss is filed against", example: "P-4471-2093" },
    { name: "loss_date", description: "Date the loss happened (YYYY-MM-DD)", example: "2026-09-28" },
    { name: "loss_description", description: "Short description in the customer's words", example: "Rear-ended at a red light on Yonge St." },
  ],
  steps: [
    step(1, "Open ClaimCenter", "Open ClaimCenter in Chrome and sign in with SSO if asked.", "Google Chrome", { type: "open_url", url: "https://claimcenter.example.com/cc/ClaimCenter.do" }, {
      shot: { app: "Chrome", window: "ClaimCenter — Desktop", url: "claimcenter.example.com/cc/ClaimCenter.do", fields: [["Search claims", ""]], highlight: "New Claim" },
    }),
    step(2, "Start a new claim", "From the Claim menu, choose New Claim.", "Google Chrome", { type: "menu", target: btn("New Claim", "ClaimCenter") }),
    step(3, "Find the policy", "Enter {{policy_number}} in Policy # and click Search.", "Google Chrome", { type: "type", target: field("Policy #", "New Claim"), text: "{{policy_number}}" }, {
      shot: { app: "Chrome", window: "New Claim — Step 1 of 4: Search policy", url: "claimcenter.example.com/cc/NewClaimWizard", fields: [["Policy #", "P-4471-2093"], ["Loss date", "2026-09-28"]], highlight: "Search" },
      expect: { window_title_contains: "Step 1", element_present: null },
    }),
    step(4, "Enter the loss date", "Set Date of Loss to {{loss_date}} and choose Loss Type: Auto.", "Google Chrome", { type: "type", target: field("Date of Loss", "New Claim"), text: "{{loss_date}}" }),
    step(5, "Describe what happened", "In Loss Description, paste {{loss_description}}. Keep it in the customer's words.", "Google Chrome", { type: "type", target: field("Loss Description", "New Claim"), text: "{{loss_description}}" }, {
      shot: { app: "Chrome", window: "New Claim — Step 2 of 4: Basic info", url: "claimcenter.example.com/cc/NewClaimWizard", fields: [["Loss description", "Rear-ended at a red light on Yonge St."], ["Loss cause", "Collision"], ["Reported by", "Insured"]], highlight: "Next" },
    }),
    step(6, "Assign to the intake queue", "Under Assignment, pick Auto Intake – Ontario.", "Google Chrome", { type: "click", target: btn("Auto Intake – Ontario", "New Claim") }),
    step(7, "Submit the claim", "Click Finish to create the claim. ClaimCenter emails the customer a claim number — this can't be undone.", "Google Chrome", { type: "click", target: btn("Finish", "New Claim") }, {
      irreversible: true,
      shot: { app: "Chrome", window: "New Claim — Step 4 of 4: Review", url: "claimcenter.example.com/cc/NewClaimWizard", fields: [["Policy", "P-4471-2093 · Personal Auto"], ["Assigned to", "Auto Intake – Ontario"]], highlight: "Finish", danger: true },
      expect: { window_title_contains: "Claim #", element_present: { role: "AXStaticText", label: "Saved" } },
    }),
  ],
  tags: ["claims", "fnol", "auto", "intake"],
};

const endorsement: SkillContent = {
  title: "Add a driver to an auto policy (endorsement)",
  goal: "Issue a mid-term endorsement in PolicyCenter that adds a newly licensed driver and sends the updated pink slip.",
  apps: ["Google Chrome", "Guidewire PolicyCenter", "Outlook"],
  prerequisites: ["PolicyCenter Underwriter role", "Driver's licence number and date first licensed"],
  inputs: [
    { name: "policy_number", description: "Policy to endorse", example: "P-3310-8812" },
    { name: "driver_name", description: "Full legal name of the new driver", example: "Jordan Lee" },
    { name: "licence_number", description: "Ontario licence number", example: "L1234-56789-01234" },
    { name: "effective_date", description: "When the change takes effect", example: "2026-10-15" },
  ],
  steps: [
    step(1, "Open the policy", "Search PolicyCenter for {{policy_number}} and open the current term.", "Google Chrome", { type: "type", target: field("Policy Number", "PolicyCenter"), text: "{{policy_number}}" }, {
      shot: { app: "Chrome", window: "PolicyCenter — Policy P-3310-8812", url: "policycenter.example.com/pc/PolicyFile", fields: [["Term", "2026-03-01 → 2027-03-01"], ["Status", "In force"]], highlight: "Change Policy" },
    }),
    step(2, "Start a policy change", "Click Change Policy and set the effective date to {{effective_date}}.", "Google Chrome", { type: "click", target: btn("Change Policy", "PolicyCenter") }),
    step(3, "Add the driver", "On the Drivers screen, click Add and enter {{driver_name}} with licence {{licence_number}}.", "Google Chrome", { type: "click", target: btn("Add", "Drivers") }, {
      shot: { app: "Chrome", window: "Policy Change — Drivers", url: "policycenter.example.com/pc/PolicyChangeWizard", fields: [["Name", "Jordan Lee"], ["Licence #", "L1234-•••••-•••••"], ["Years licensed", "1"]], highlight: "Add Driver" },
    }),
    step(4, "Quote the change", "Click Quote and check the premium difference with the customer.", "Google Chrome", { type: "click", target: btn("Quote", "Policy Change") }),
    step(5, "Bind the endorsement", "Click Issue Policy Change. The new premium is billed on the next instalment.", "Google Chrome", { type: "click", target: btn("Issue Policy Change", "Policy Change") }, {
      irreversible: true,
      shot: { app: "Chrome", window: "Policy Change — Quote", url: "policycenter.example.com/pc/PolicyChangeWizard", fields: [["Premium change", "+ $412.00 / term"], ["Effective", "2026-10-15"]], highlight: "Issue Policy Change", danger: true },
    }),
    step(6, "Email the pink slip", "In Outlook, reply to the customer's request and attach the new liability slip from Documents.", "Outlook", { type: "open_app" }),
  ],
  tags: ["policy", "endorsement", "drivers", "underwriting"],
};

const brokerReset: SkillContent = {
  title: "Reset a broker portal password",
  goal: "Verify a broker's identity, send them a password reset link for the broker portal, and log the request.",
  apps: ["Google Chrome", "Broker Admin Console", "Outlook"],
  prerequisites: ["Broker Admin Console access", "Ticket from the broker's verified email"],
  inputs: [
    { name: "broker_email", description: "Broker's email on file", example: "agent@northshore-brokers.example" },
    { name: "brokerage_code", description: "Five-character brokerage code", example: "NSB41" },
  ],
  steps: [
    step(1, "Find the broker", "Open Broker Admin Console and search for {{broker_email}}.", "Google Chrome", { type: "type", target: field("Search users", "Broker Admin"), text: "{{broker_email}}" }, {
      shot: { app: "Chrome", window: "Broker Admin — Users", url: "brokeradmin.example.com/users", fields: [["Email", "agent@northshore-brokers.example"], ["Brokerage", "NSB41 · Northshore"]], highlight: "Open profile" },
    }),
    step(2, "Confirm the brokerage code", "Check the profile shows brokerage {{brokerage_code}}. Stop if it doesn't match.", "Google Chrome", { type: "wait" }),
    step(3, "Send the reset link", "Click Send password reset. The link expires in 30 minutes.", "Google Chrome", { type: "click", target: btn("Send password reset", "Broker profile") }, {
      shot: { app: "Chrome", window: "Broker profile — Security", url: "brokeradmin.example.com/users/8812/security", fields: [["MFA", "Enabled"], ["Last sign-in", "12 days ago"]], highlight: "Send password reset" },
    }),
    step(4, "Sign out other sessions", "Click Revoke all sessions so the old password stops working everywhere.", "Google Chrome", { type: "click", target: btn("Revoke all sessions", "Broker profile") }, { irreversible: true }),
    step(5, "Close the ticket", "Reply to the ticket in Outlook with the standard reset template.", "Outlook", { type: "key", key: "cmd+r" }),
  ],
  tags: ["broker", "password", "access", "support"],
};

const ubiTrip: SkillContent = {
  title: "Export a UBI trip summary for a customer",
  goal: "Pull a customer's last 90 days of driving trips from the telematics console and export a PDF summary for their file.",
  apps: ["Google Chrome", "Telematics Console"],
  prerequisites: ["Telematics Console read access", "Customer consent on file"],
  inputs: [{ name: "policy_number", description: "UBI-enrolled policy", example: "P-9021-4410" }],
  steps: [
    step(1, "Open the enrolment", "Search the Telematics Console for {{policy_number}}.", "Google Chrome", { type: "type", target: field("Policy", "Telematics"), text: "{{policy_number}}" }),
    step(2, "Filter to 90 days", "Set the date range to Last 90 days.", "Google Chrome", { type: "click", target: btn("Last 90 days", "Trips") }, {
      shot: { app: "Chrome", window: "Trips — P-9021-4410", url: "telematics.example.com/enrolments/9021/trips", fields: [["Trips", "142"], ["Hard brakes / 100 km", "0.8"]], highlight: "Export PDF" },
    }),
    step(3, "Export the PDF", "Click Export PDF and save it to the customer's file.", "Google Chrome", { type: "click", target: btn("Export PDF", "Trips") }),
  ],
  tags: ["ubi", "telematics", "customer"],
};

const rentalDraft: SkillContent = {
  title: "Book a rental car for a claimant",
  goal: "Reserve a replacement vehicle through the rental partner portal for a covered auto claim.",
  apps: ["Google Chrome", "Rental Partner Portal"],
  prerequisites: ["Claim has rental coverage (OPCF 20)"],
  inputs: [
    { name: "claim_number", description: "Open auto claim", example: "C-26-118402" },
    { name: "pickup_branch", description: "Branch closest to the claimant", example: "Toronto – Queen St E" },
  ],
  steps: [
    step(1, "Open the rental portal", "Go to the rental partner portal and sign in.", "Google Chrome", { type: "open_url", url: "https://rentals.example.com/insurer" }, {
      shot: { app: "Chrome", window: "Rental Partner — New reservation", url: "rentals.example.com/insurer/new", fields: [["Claim #", "C-26-118402"], ["Branch", "Toronto – Queen St E"]], highlight: "Check availability" },
    }),
    step(2, "Enter the claim", "Type the claim number C-26-118402 into Claim #.", "Google Chrome", { type: "type", target: field("Claim #", "Rental Partner"), text: "C-26-118402" }),
    {
      ...step(3, "Open Slack", "Open or switch to Slack.", "Slack", { type: "open_app" }),
      excluded: true,
      filter: { decision: "drop", reason: "detour", p_drop: 0.96, source: "jev" },
      source_seqs: [9],
    },
    {
      ...step(4, "Click “#claims-ontario”", "Click the “#claims-ontario” link.", "Slack", { type: "click", target: btn("#claims-ontario", "Slack") }),
      excluded: true,
      filter: { decision: "drop", reason: "detour", p_drop: 0.94, source: "jev" },
      source_seqs: [10],
    },
    {
      ...step(5, "Click “Vehicle class”", "Click the “Vehicle class” dropdown.", "Google Chrome", { type: "click", target: btn("Vehicle class", "Rental Partner") }),
      filter: { decision: "review", reason: "exploration", p_drop: 0.71, source: "jev+local" },
      source_seqs: [12, 13],
    },
    step(6, "Clicked Check availability", "Click Check availability.", "Google Chrome", { type: "click", target: btn("Check availability", "Rental Partner") }),
    step(7, "Confirm reservation", "Click Confirm reservation.", "Google Chrome", { type: "click", target: btn("Confirm reservation", "Rental Partner") }, {
      shot: { app: "Chrome", window: "Rental Partner — Confirm", url: "rentals.example.com/insurer/confirm", fields: [["Vehicle", "Compact · up to 30 days"], ["Daily rate", "$45.00"]], highlight: "Confirm reservation", danger: true },
    }),
  ],
  tags: ["claims", "rental"],
};

const lossRatio: SkillContent = {
  title: "Refresh the weekly claims intake report",
  goal: "Refresh the intake dashboard export and send the Monday summary to team leads.",
  apps: ["Microsoft Excel", "Outlook"],
  prerequisites: ["Access to the Claims Ops shared drive"],
  inputs: [],
  steps: [
    step(1, "Open the workbook", "Open Weekly Intake.xlsx from the Claims Ops drive.", "Microsoft Excel", { type: "open_app" }),
    step(2, "Refresh data", "Choose Data › Refresh All and wait for the queries to finish.", "Microsoft Excel", { type: "menu", target: btn("Refresh All", "Weekly Intake.xlsx") }),
    step(3, "Send the summary", "Copy the Summary tab into a new Outlook email to Claims Leads and send.", "Outlook", { type: "key", key: "cmd+enter" }),
  ],
  tags: ["reporting", "claims"],
};

interface Rec {
  skill: Skill;
  versions: SkillVersion[];
}
const skills = new Map<ID, Rec>();

function seedSkill(
  id: ID,
  content: SkillContent,
  o: { owner: UserRef; team: TeamRef | null; versions: number; health: Skill["health"]; status?: Skill["status"]; updated: number; draft?: boolean; visibility?: Skill["visibility"]; source?: ID | null },
) {
  const versions: SkillVersion[] = [];
  for (let v = 1; v <= o.versions; v++) {
    const c = clone(content);
    if (v < o.versions) c.steps = c.steps.slice(0, Math.max(2, c.steps.length - (o.versions - v)));
    versions.push({ version: v, content: c, created_at: ago(o.updated + (o.versions - v) * 60 * 24 * 9), created_by: o.owner });
  }
  skills.set(id, {
    versions,
    skill: {
      id,
      owner: o.owner,
      team: o.team,
      visibility: o.visibility ?? "org",
      status: o.status ?? (o.versions ? "published" : "draft"),
      current_version: o.versions,
      draft: o.draft ? clone(content) : null,
      published: o.versions ? clone(content) : null,
      source_recording_id: o.source ?? null,
      created_at: ago(o.updated + 60 * 24 * 40),
      updated_at: ago(o.updated),
      health: o.health,
    },
  });
}

const S = {
  fnol: "5f1c0e2a-0001-4000-8000-000000000001",
  endo: "5f1c0e2a-0002-4000-8000-000000000002",
  broker: "5f1c0e2a-0003-4000-8000-000000000003",
  ubi: "5f1c0e2a-0004-4000-8000-000000000004",
  rental: "5f1c0e2a-0005-4000-8000-000000000005",
  report: "5f1c0e2a-0006-4000-8000-000000000006",
};
const R = { rental: "9a7d3b10-0001-4000-8000-0000000000a1" };

seedSkill(S.fnol, fnol, { owner: P.priya, team: T.claims, versions: 3, updated: 60 * 26, health: { runs: 24, success_rate: 0.92, last_run_at: ago(95) } });
seedSkill(S.endo, endorsement, { owner: P.marc, team: T.pl, versions: 2, updated: 60 * 24 * 6, health: { runs: 11, success_rate: 0.73, last_run_at: ago(60 * 30) } });
seedSkill(S.broker, brokerReset, { owner: P.aisha, team: T.broker, versions: 5, updated: 60 * 24 * 2, health: { runs: 63, success_rate: 0.98, last_run_at: ago(22) } });
seedSkill(S.ubi, ubiTrip, { owner: { id: "u-me", name: "Dharmik", email: "dharmik@example.com", avatar: "orb" }, team: T.ubi, versions: 1, updated: 60 * 5, visibility: "team", health: { runs: 0, success_rate: null, last_run_at: null } });
seedSkill(S.rental, rentalDraft, { owner: { id: "u-me", name: "Dharmik", email: "dharmik@example.com", avatar: "orb" }, team: T.claims, versions: 0, updated: 14, draft: true, visibility: "private", source: R.rental, health: { runs: 0, success_rate: null, last_run_at: null } });
seedSkill(S.report, lossRatio, { owner: P.priya, team: T.claims, versions: 1, updated: 60 * 24 * 21, visibility: "team", status: "archived", health: { runs: 4, success_rate: 0.5, last_run_at: ago(60 * 24 * 22) } });

// ---------- recordings ----------
const startedProcessing = Date.now();
const recordings: Recording[] = [
  { id: "9a7d3b10-0004-4000-8000-0000000000a4", status: "received", event_count: 212, created_at: ago(0.5), title_hint: "Update mailing address on a policy" },
  { id: "9a7d3b10-0003-4000-8000-0000000000a3", status: "processing", event_count: 87, created_at: ago(3), title_hint: "Approve a glass claim payment" },
  {
    id: R.rental, status: "ready", skill_id: S.rental, skill_ids: [S.rental], event_count: 46, created_at: ago(16), title_hint: "Rental car booking",
    intent: "Booked a rental car for a claimant through the partner portal",
    filter: { source: "jev", counts: { keep: 15, review: 2, drop: 2 }, segments: 1, task_type: null },
  },
  { id: "9a7d3b10-0002-4000-8000-0000000000a2", status: "failed", error: "The recording only had scroll events, so there were no steps to turn into a skill. Record again and click through the task.", event_count: 9, created_at: ago(60 * 26) },
  { id: "9a7d3b10-0005-4000-8000-0000000000a5", status: "ready", skill_id: S.ubi, event_count: 31, created_at: ago(60 * 30), title_hint: "UBI trip export" },
];
function tickRecordings() {
  const age = Date.now() - startedProcessing;
  for (const r of recordings) {
    if (r.status === "received" && age > 6000) r.status = "processing";
    else if (r.status === "processing" && age > 14000 && r.id.endsWith("a3")) {
      r.status = "failed";
      r.error = "The draft generator timed out. It will retry automatically.";
    }
  }
}

// ---------- runs ----------
const runs: Run[] = [];
(function seedRuns() {
  const people = [P.marc, P.aisha, meRef(), P.priya];
  const fnolSteps = fnol.steps.length;
  const outcomes: Array<{ status: Run["status"]; fail?: number; repair?: number }> = [
    { status: "succeeded" }, { status: "succeeded", repair: 3 }, { status: "failed", fail: 6 }, { status: "succeeded" },
    { status: "aborted", fail: 7 }, { status: "succeeded" }, { status: "succeeded", repair: 5 }, { status: "succeeded" },
  ];
  outcomes.forEach((o, i) => {
    const started = 95 + i * 60 * 7;
    const stepsDone = o.fail ?? fnolSteps;
    runs.push({
      id: uid("7b"),
      skill_id: S.fnol,
      version: i < 3 ? 3 : 2,
      mode: i % 3 === 0 ? "guided" : "auto",
      status: o.status,
      user: people[i % people.length],
      started_at: ago(started),
      finished_at: ago(started - 2),
      error: o.status === "failed" ? "Couldn't find “Auto Intake – Ontario” after the queue list was renamed." : o.status === "aborted" ? "Stopped by user at the confirmation step." : null,
      steps: Array.from({ length: stepsDone }, (_, k) => {
        const idx = k + 1;
        const isFail = o.fail === idx;
        const isRepair = o.repair === idx;
        const irr = fnol.steps[k]?.irreversible;
        return {
          step_index: idx,
          status: isFail ? (o.status === "aborted" ? "skipped" : "failed") : isRepair ? "repaired" : irr ? "confirmed" : "ok",
          strategy: isRepair ? "llm_repair" : irr ? "human" : isFail ? "llm_repair" : "deterministic",
          duration_ms: Math.round(400 + ((idx * 937 + i * 311) % 2600)),
          detail: isRepair ? "Button label changed from “Search” to “Find policy”." : null,
        };
      }),
    });
  });
})();

// ---------- admin ----------
let flags: Flags = {
  recording_enabled: true, replay_enabled: true, llm_enabled: true, max_recording_minutes: 20, screenshot_policy: "key_moments",
  filter_enabled: true, filter_drop_threshold: 0.9, filter_review_threshold: 0.6, split_tasks_enabled: true,
};
const jobs: Job[] = [
  { id: "j-0001-dead", kind: "draft_from_recording", status: "dead", attempts: 5, max_attempts: 5, last_error: "anthropic: 529 overloaded (after 5 attempts)", created_at: ago(60 * 7), updated_at: ago(60 * 2) },
  { id: "j-0002-dead", kind: "embed_skill", status: "dead", attempts: 5, max_attempts: 5, last_error: "openai embeddings: connection reset by peer", created_at: ago(60 * 30), updated_at: ago(60 * 28) },
];

// ---------- helpers ----------
function toSummary(s: Skill): SkillSummary {
  const c = (s.draft && (s.status === "draft" || !s.published) ? s.draft : s.published) ?? s.draft!;
  return {
    id: s.id,
    title: c.title,
    goal: c.goal,
    owner: s.owner,
    team: s.team,
    visibility: s.visibility,
    status: s.status,
    tags: c.tags,
    apps: c.apps,
    current_version: s.current_version,
    updated_at: s.updated_at,
    health: s.health,
  };
}

function canSee(s: Skill) {
  if (me.role === "admin") return true;
  if (s.owner.id === me.id) return true;
  if (s.visibility === "org") return s.status !== "draft";
  if (s.visibility === "team") return !!s.team && me.teams.some((t) => t.id === s.team!.id);
  return false;
}

function requireAuth() {
  if (!getToken()) throw new ApiError(401, "unauthorized", "Sign in to continue.");
}

function get(id: ID): Rec {
  const r = skills.get(id);
  if (!r || !canSee(r.skill)) throw new ApiError(404, "not_found", "That skill doesn't exist or isn't shared with you.");
  return r;
}

function requireOwner(s: Skill) {
  if (s.owner.id !== me.id && me.role !== "admin") throw new ApiError(403, "forbidden", "Only the owner can change this skill.");
}

function tokens(s: string) {
  return s.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 1 && !["how", "do", "the", "to", "for", "an", "of", "on", "in", "my", "and"].includes(w));
}

// ---------- client ----------
export class MockApiClient implements ApiClient {
  async publicConfig() {
    await wait(60);
    return { auth_mode: "dev" as const, oidc: null };
  }
  async devLogin(body: DevLoginRequest) {
    await wait();
    if (!body.email.includes("@")) throw new ApiError(422, "invalid_email", "Enter a work email address.");
    const team = body.team?.trim();
    const known = Object.values(T).find((t) => t.name.toLowerCase() === team?.toLowerCase());
    me = {
      ...me,
      email: body.email,
      name: body.name || body.email.split("@")[0],
      teams: team ? [known ?? { id: "t-new", name: team }, ...me.teams.filter((t) => t.id !== known?.id)] : me.teams,
    };
    return { token: "mock-token-" + Math.random().toString(36).slice(2), user: clone(me) };
  }
  async me() {
    await wait(60);
    requireAuth();
    return clone(me);
  }
  async updateMe(body: { avatar: AvatarKind }) {
    await wait(200);
    requireAuth();
    me = { ...me, avatar: body.avatar };
    // the signed-in user's own skills show the new character too
    for (const r of skills.values()) if (r.skill.owner.id === me.id) r.skill.owner = { ...r.skill.owner, avatar: body.avatar };
    return clone(me);
  }
  async teams() {
    await wait();
    return { items: Object.values(T) };
  }
  async config() {
    await wait();
    return clone(flags);
  }

  async assetBlob(sha256: string) {
    await wait(200 + Math.random() * 400);
    const s = shots.get(sha256);
    if (!s) throw new ApiError(404, "not_found", "Screenshot not found.");
    return new Blob([renderShot(s)], { type: "image/svg+xml" });
  }

  async listRecordings() {
    await wait();
    tickRecordings();
    return { items: clone(recordings), next_cursor: null };
  }
  async getRecording(id: ID) {
    await wait();
    tickRecordings();
    const r = recordings.find((x) => x.id === id);
    if (!r) throw new ApiError(404, "not_found", "Recording not found.");
    return clone(r);
  }

  async listSkills(p: ListSkillsParams = {}) {
    await wait();
    requireAuth();
    let list = [...skills.values()].map((r) => r.skill).filter(canSee);
    if (p.mine) list = list.filter((s) => s.owner.id === me.id);
    if (p.status) list = list.filter((s) => s.status === p.status);
    else list = list.filter((s) => s.status !== "archived");
    if (p.team_id) list = list.filter((s) => s.team?.id === p.team_id);
    if (p.q) {
      const q = p.q.toLowerCase();
      list = list.filter((s) => JSON.stringify(toSummary(s)).toLowerCase().includes(q));
    }
    list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return { items: clone(list.map(toSummary)), next_cursor: null };
  }

  async search(q: string, limit = 20) {
    await wait(180 + Math.random() * 220);
    requireAuth();
    const qt = tokens(q);
    const hits: SearchHit[] = [];
    for (const { skill } of skills.values()) {
      if (skill.status !== "published" || !canSee(skill) || !skill.published) continue;
      const c = skill.published;
      const hay = tokens([c.title, c.goal, c.tags.join(" "), c.apps.join(" "), c.steps.map((s) => s.title).join(" ")].join(" "));
      const title = tokens(c.title);
      let score = 0;
      for (const t of qt) {
        if (title.some((w) => w.startsWith(t))) score += 2;
        else if (hay.some((w) => w.startsWith(t) || t.startsWith(w))) score += 1;
      }
      if (score > 0) hits.push({ ...toSummary(skill), score: Math.min(0.99, score / (qt.length * 2 || 1)) });
    }
    hits.sort((a, b) => b.score - a.score);
    return { items: clone(hits.slice(0, limit)) };
  }

  async createSkill(body: CreateSkillRequest) {
    await wait();
    const id = uid("5f1c");
    const team = Object.values(T).find((t) => t.id === body.team_id) ?? null;
    const skill: Skill = {
      id,
      owner: meRef(),
      team,
      visibility: body.visibility,
      status: "draft",
      current_version: 0,
      draft: clone(body.content),
      published: null,
      source_recording_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      health: { runs: 0, success_rate: null, last_run_at: null },
    };
    skills.set(id, { skill, versions: [] });
    return clone(skill);
  }
  async getSkill(id: ID) {
    await wait();
    requireAuth();
    return clone(get(id).skill);
  }
  async patchSkill(id: ID, body: PatchSkillRequest) {
    await wait();
    const r = get(id);
    requireOwner(r.skill);
    if (!r.skill.draft && r.skill.published) r.skill.draft = clone(r.skill.published);
    if (body.content) r.skill.draft = clone(body.content);
    if (body.visibility) r.skill.visibility = body.visibility;
    if (body.team_id !== undefined) r.skill.team = Object.values(T).find((t) => t.id === body.team_id) ?? null;
    r.skill.updated_at = new Date().toISOString();
    return clone(r.skill);
  }
  async publishSkill(id: ID) {
    await wait(400);
    const r = get(id);
    requireOwner(r.skill);
    const content = r.skill.draft ?? r.skill.published;
    if (!content) throw new ApiError(409, "nothing_to_publish", "There's no draft to publish.");
    // Same as the server: left-out steps are removed and filter notes dropped on publish.
    content.steps = content.steps.filter((s) => !s.excluded).map((s, i) => ({ ...s, index: i + 1, excluded: false, filter: null }));
    if (!content.steps.length) throw new ApiError(422, "skill_has_no_steps", "A skill needs at least one step to be published.");
    const version = r.skill.current_version + 1;
    r.versions.push({ version, content: clone(content), created_at: new Date().toISOString(), created_by: meRef() });
    Object.assign(r.skill, { current_version: version, published: clone(content), draft: null, status: "published", updated_at: new Date().toISOString() });
    return clone(r.skill);
  }
  async archiveSkill(id: ID) {
    await wait();
    const r = get(id);
    requireOwner(r.skill);
    r.skill.status = "archived";
    return clone(r.skill);
  }
  async listVersions(id: ID) {
    await wait();
    return { items: get(id).versions.map(({ version, created_at, created_by }) => ({ version, created_at, created_by })).reverse() };
  }
  async getVersion(id: ID, n: number) {
    await wait();
    const v = get(id).versions.find((x) => x.version === n);
    if (!v) throw new ApiError(404, "not_found", `Version ${n} doesn't exist.`);
    return clone(v);
  }
  async suggestFix() {
    await wait();
    return { ok: true };
  }

  async createRun(body: CreateRunRequest) {
    await wait();
    const id = uid("7b");
    runs.unshift({ id, skill_id: body.skill_id, version: body.version, mode: body.mode, status: "running", user: meRef(), started_at: new Date().toISOString(), steps: [] });
    return { id };
  }
  async reportRunStep(runId: ID, body: RunStepReport) {
    await wait();
    runs.find((r) => r.id === runId)?.steps?.push(body);
    return {};
  }
  async listRuns(skillId: ID) {
    await wait();
    get(skillId);
    return { items: clone(runs.filter((r) => r.skill_id === skillId)), next_cursor: null };
  }

  private requireAdmin() {
    if (me.role !== "admin") throw new ApiError(403, "forbidden", "Admins only.");
  }
  async getAdminFlags() {
    await wait();
    this.requireAdmin();
    return clone(flags);
  }
  async putAdminFlags(f: Flags) {
    await wait(300);
    this.requireAdmin();
    flags = clone(f);
    return clone(flags);
  }
  async usage(days = 30): Promise<Usage> {
    await wait();
    this.requireAdmin();
    const k = days / 30;
    return {
      llm_calls: Math.round(1284 * k),
      input_tokens: Math.round(9_412_000 * k),
      output_tokens: Math.round(1_208_500 * k),
      est_cost_usd: Math.round(46.18 * k * 100) / 100,
      skills_created: Math.round(37 * k),
      runs: Math.round(412 * k),
      run_success_rate: 0.89,
      by_team: [
        { team_id: T.claims.id, team_name: T.claims.name, llm_calls: Math.round(522 * k), est_cost_usd: 18.9 * k },
        { team_id: T.broker.id, team_name: T.broker.name, llm_calls: Math.round(341 * k), est_cost_usd: 12.05 * k },
        { team_id: T.pl.id, team_name: T.pl.name, llm_calls: Math.round(268 * k), est_cost_usd: 9.88 * k },
        { team_id: T.ubi.id, team_name: T.ubi.name, llm_calls: Math.round(153 * k), est_cost_usd: 5.35 * k },
      ],
    };
  }
  async filterStats(days = 30): Promise<FilterStats> {
    await wait();
    this.requireAdmin();
    const k = days / 30;
    const n = (x: number) => Math.round(x * k);
    return {
      days,
      provider: "jev",
      events: n(18_420),
      reviewed: n(12_960),
      decisions: { keep: n(15_870), review: n(1_410), drop: n(1_140) },
      matrix: [
        { decision: "drop", final_keep: false, n: n(742) },
        { decision: "drop", final_keep: true, n: n(31) },
        { decision: "keep", final_keep: false, n: n(118) },
        { decision: "keep", final_keep: true, n: n(11_102) },
        { decision: "review", final_keep: false, n: n(604) },
        { decision: "review", final_keep: true, n: n(363) },
      ],
      wrongly_dropped_rate: 0.0401,
      missed_rate: 0.0105,
      by_reason: [
        { reason: "detour", n: n(1_202), restored: n(48) },
        { reason: "exploration", n: n(688), restored: n(201) },
        { reason: "mistake_undone", n: n(341), restored: n(9) },
        { reason: "duplicate", n: n(212), restored: n(41) },
        { reason: "unclear_relevance", n: n(107), restored: n(64) },
      ],
      by_source: [{ source: "jev", n: n(17_830) }, { source: "jev+local", n: n(402) }, { source: "local", n: n(188) }],
      threshold_curve: [
        { threshold: 0.5, flagged: n(2_310), precision: 0.62, recall: 0.97 },
        { threshold: 0.6, flagged: n(1_740), precision: 0.77, recall: 0.92 },
        { threshold: 0.7, flagged: n(1_420), precision: 0.85, recall: 0.87 },
        { threshold: 0.75, flagged: n(1_260), precision: 0.89, recall: 0.82 },
        { threshold: 0.8, flagged: n(1_090), precision: 0.92, recall: 0.75 },
        { threshold: 0.85, flagged: n(930), precision: 0.94, recall: 0.66 },
        { threshold: 0.9, flagged: n(773), precision: 0.96, recall: 0.56 },
        { threshold: 0.95, flagged: n(512), precision: 0.98, recall: 0.38 },
      ],
      jev: { recordings: n(611), partial_failures: n(4), est_cost_usd: Math.round(0.71 * k * 100) / 100 },
    };
  }
  async filterExport() {
    await wait();
    this.requireAdmin();
    return new Blob(['{"seq":1,"decision":"keep","final_keep":true}\n'], { type: "application/x-ndjson" });
  }
  async deadJobs() {
    await wait();
    this.requireAdmin();
    return { items: clone(jobs.filter((j) => j.status === "dead")) };
  }
  async retryJob(id: ID) {
    await wait(300);
    this.requireAdmin();
    const j = jobs.find((x) => x.id === id);
    if (j) j.status = "queued";
    return { ok: true };
  }
}
