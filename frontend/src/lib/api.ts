import { readToken } from "@/lib/session";

/**
 * Backend client.
 *
 * Architecture §13: this layer transports values. It does not compute them.
 * Monetary values arrive as strings (API spec §1) and stay strings until a
 * formatter renders them — parsing them into JavaScript numbers would silently
 * lose the precision the reconciliation gate depends on.
 *
 * Every call runs on the server, so the bearer token never reaches the
 * browser (see `lib/session.ts`).
 */

/**
 * Where the API lives, read at request time.
 *
 * Deliberately **not** `NEXT_PUBLIC_`-prefixed. Next inlines those into the
 * build output, and this file only ever runs on the server — so a container
 * built without the variable baked in `http://localhost:8000` and ignored
 * whatever the host set at runtime. On any platform that builds the image
 * before the API's URL exists, the web app could never reach the API.
 *
 * `NEXT_PUBLIC_API_URL` is still honoured so existing local setups and the
 * compose files keep working.
 *
 * A trailing slash is stripped. Every caller does `${apiBaseUrl()}/api${path}`,
 * so a value pasted from a browser's address bar with its trailing `/` — the
 * ordinary way to copy a Render service's URL — produced `…//api/auth/…`.
 * FastAPI does not collapse a doubled slash, so that request matched no
 * route and came back a genuine `404 Not Found`, which the client then
 * displayed verbatim: indistinguishable from the app being unreachable,
 * merely from one keystroke in how the URL was copied.
 */
function apiBaseUrl(): string {
  const raw =
    process.env.IFRS18_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The RFC 9457 `type` slug, e.g. `reconciliation-failed`. */
    readonly code: string | null = null,
    readonly body: ProblemDocument | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ProblemDocument {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  [key: string]: unknown;
}

function problemCode(body: ProblemDocument | null): string | null {
  const type = body?.type;
  if (typeof type !== "string") return null;
  const slug = type.split("/").pop();
  return slug ?? null;
}

interface RequestOptions extends RequestInit {
  /** Send the caller's session token. Off for the public catalog endpoints. */
  authenticated?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { authenticated = true, headers, ...init } = options;
  const token = authenticated ? await readToken() : null;

  const response = await fetch(`${apiBaseUrl()}/api${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let body: ProblemDocument | null = null;
    try {
      body = (await response.json()) as ProblemDocument;
    } catch {
      body = null;
    }
    throw new ApiError(
      body?.detail ?? body?.title ?? `Request to ${path} failed`,
      response.status,
      problemCode(body),
      body,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(payload: unknown): RequestOptions {
  return {
    method: "POST",
    body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  };
}

// ---------------------------------------------------------------------------
// Types. Mirrors of the response models in `app/api/schemas`.
// Every monetary field is a string, deliberately (API spec §1).
// ---------------------------------------------------------------------------

export interface Disclaimer {
  disclaimer_en: string;
  disclaimer_ko: string;
  limitations_en: string[];
}

export interface Health {
  status: "ok";
  environment: string;
  version: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface Company {
  id: string;
  name: string;
  identifier: string | null;
  identifier_scheme: string | null;
  jurisdiction: string;
}

export interface BlockingReason {
  code: string;
  count: number;
  detail: string | null;
}

export interface ProjectProgress {
  lines_total: number;
  lines_classified: number;
  requires_review: number;
  reviewed: number;
  open_questions: number;
  can_finalize: boolean;
  blocking_reasons: BlockingReason[];
}

export type ProjectStatus =
  | "DRAFT"
  | "UPLOADED"
  | "EXTRACTED"
  | "EXTRACTION_FAILED"
  | "CLASSIFIED"
  | "IN_REVIEW"
  | "RECONCILIATION_FAILED"
  | "FINALIZED";

export type ReconciliationStatus = "NOT_RUN" | "PASSED" | "FAILED";

export interface Project {
  id: string;
  name: string;
  company: Company;
  fiscal_year: number;
  period_start: string;
  period_end: string;
  basis: string;
  presentation_currency: string;
  presentation_scale: number;
  status: ProjectStatus;
  reconciliation_status: ReconciliationStatus;
  rule_set_version: string | null;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
  progress?: ProjectProgress;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number | null;
}

export interface Upload {
  id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  scan_status: string;
  parse_status: string;
  parse_error: string | null;
  created_at: string;
}

export interface ExtractionCheck {
  check: string;
  reported: string;
  computed: string;
  delta: string;
  tolerance: string;
  passed: boolean;
}

export interface ExtractionReport {
  passed: boolean;
  blockers: string[];
  signs_inferred: boolean;
  checks: ExtractionCheck[];
}

export interface SourceLocator {
  source_file: string;
  sheet: string | null;
  row: number | null;
  column: string | null;
  cell: string | null;
  page: number | null;
}

export interface Line {
  id: string;
  ordinal: number;
  depth: number;
  raw_label: string;
  raw_value: string;
  amount: string;
  sign_normalization: string;
  is_subtotal: boolean;
  subtotal_kind: string | null;
  decomposition_status: string;
  parent_line_id: string | null;
  normalized_account_code: string | null;
  note_references: string[];
  source_locator: SourceLocator;
}

export interface LinesResponse {
  items: Line[];
  report: ExtractionReport | null;
}

export interface ExtractResult {
  statement: { id: string; currency: string; scale: number };
  line_count: number;
  report: ExtractionReport;
}

export type Ifrs18Category =
  | "OPERATING"
  | "INVESTING"
  | "FINANCING"
  | "INCOME_TAX"
  | "DISCONTINUED_OPERATION"
  | "UNCLASSIFIED";

export interface Evidence {
  evidence_type: string;
  reference: string;
  excerpt: string | null;
  produced_by: string;
}

export interface Classification {
  id: string;
  line_id: string;
  original_account: string;
  normalized_account_code: string | null;
  amount: string;
  current_category: string | null;
  proposed_ifrs18_category: Ifrs18Category | null;
  proposed_ifrs18_subcategory: string | null;
  final_ifrs18_category: Ifrs18Category | null;
  final_ifrs18_subcategory: string | null;
  classification_method: string;
  rule_id: string | null;
  rule_source_reference: string | null;
  rule_verification_status: string | null;
  ai_model: string | null;
  ai_confidence: string | null;
  ai_reasoning: string | null;
  confidence_band: string | null;
  requires_human_review: boolean;
  blocked_on_question_id: string | null;
  user_override: boolean;
  override_reason: string | null;
  reviewed_at: string | null;
  reviewer_user_id: string | null;
  impact_on_operating_profit: string | null;
  evidence: Evidence[];
}

export interface Rule {
  rule_id: string;
  priority: number;
  description: string;
  source_type: string;
  source_reference: string;
  verification_status: string;
  source_url: string | null;
  source_note: string | null;
}

export interface Review {
  action: string;
  reviewer_user_id: string;
  previous_category: Ifrs18Category | null;
  new_category: Ifrs18Category | null;
  reason: string | null;
  reviewed_at: string;
}

export interface ClassificationDetail extends Classification {
  raw_label: string;
  raw_value: string;
  source_locator: Partial<SourceLocator>;
  rule: Rule | null;
  question: Question | null;
  reviews: Review[];
}

export interface ClassificationSummary {
  total: number;
  by_method: Record<string, number>;
  by_category: Record<string, number>;
  requires_review: number;
  unreviewed: number;
  open_questions: number;
}

export interface ClassificationsResponse {
  items: Classification[];
  summary: ClassificationSummary;
}

export interface ClassifyResult {
  summary: ClassificationSummary;
  questions: Question[];
  rule_set_version: string;
  preserved_overrides: number;
  discarded: number;
  ai_assistant_available: boolean;
}

export interface Question {
  id: string;
  scope: "COMPANY" | "LINE";
  line_id: string | null;
  question_key: string;
  question_text_ko: string;
  question_text_en: string;
  help_ko: string | null;
  help_en: string | null;
  raised_by_rule_id: string | null;
  options: string[];
  allows_undue_cost_or_effort: boolean;
  answer: string | null;
  resolved_category: Ifrs18Category | null;
  undue_cost_or_effort: boolean;
  note: string | null;
  answered_at: string | null;
  is_resolved: boolean;
  blocks_finalization: boolean;
  affected_line_count: number;
  affected_amount: string | null;
}

export interface QuestionsResponse {
  items: Question[];
  open_count: number;
}

export interface BusinessActivity {
  id: string;
  activity_type: string;
  is_main_business_activity: boolean | null;
  description: string | null;
  source: string;
  confirmed_by_user: boolean;
  confirmed_at: string | null;
  is_specified: boolean;
}

export interface Check {
  check: string;
  passed: boolean;
  severity: "BLOCKING" | "WARNING";
  detail: string;
  expected: string | null;
  actual: string | null;
  delta: string | null;
  tolerance: string;
}

export interface Reconciliation {
  status: ReconciliationStatus;
  passed: boolean;
  checks: Check[];
  blocking_failures: string[];
  warnings: string[];
}

export interface StatementLine {
  line_id: string | null;
  classification_id: string | null;
  label_ko: string;
  amount: string;
  normalized_account_code: string | null;
  rule_id: string | null;
  user_override: boolean;
}

export interface Section {
  category: Ifrs18Category;
  label_en: string;
  label_ko: string;
  lines: StatementLine[];
  total: string;
}

export interface Subtotal {
  key: string;
  label_en: string;
  label_ko: string;
  amount: string;
  presented: boolean;
  suppressed_reason: string | null;
}

export interface Statement {
  reconciliation: Reconciliation;
  currency: string;
  scale: number;
  sections: Section[];
  subtotals: Subtotal[];
  disclaimer: string;
  limitations: string[];
}

export interface Kpi {
  key: string;
  unit: "AMOUNT" | "PERCENT";
  before: string | null;
  after: string;
  change: string | null;
  change_pct: string | null;
}

export interface WaterfallStep {
  kind: "START" | "DELTA" | "UNATTRIBUTED" | "END";
  key: string;
  label_ko: string;
  label_en: string;
  value: string;
  to_category: Ifrs18Category | null;
  reported_placement: string | null;
  line_ids: string[];
  classification_ids: string[];
}

export interface Reclassification {
  classification_id: string | null;
  line_id: string | null;
  original_account: string;
  amount: string;
  reported_placement: string;
  ifrs18_category: Ifrs18Category;
  impact_on_operating_profit: string;
  rule_id: string | null;
  rationale_summary: string | null;
}

export interface Impact {
  reconciliation: Reconciliation;
  currency: string;
  scale: number;
  comparable: boolean;
  headline: Kpi | null;
  kpis: Kpi[];
  waterfall: WaterfallStep[];
  waterfall_balances: boolean;
  top_reclassifications: Reclassification[];
  computed_at: string | null;
  disclaimer: string;
  limitations: string[];
}

export interface FinalizeResult {
  status: string;
  reconciliation: Reconciliation;
  impact_analysis_id: string;
  finalized_at: string | null;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_user_id: string | null;
  actor_type: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
}

export interface AuditLog {
  items: AuditEntry[];
  total: number;
}

export interface CreateProjectInput {
  name: string;
  company: { name: string; identifier?: string; identifier_scheme?: string; jurisdiction: string };
  fiscal_year: number;
  period_start: string;
  period_end: string;
  basis: string;
  presentation_currency: string;
  presentation_scale: number;
}

// ---------------------------------------------------------------------------

export const api = {
  health: () => request<Health>("/health", { authenticated: false }),
  disclaimer: () => request<Disclaimer>("/meta/disclaimer", { authenticated: false }),

  register: (email: string, password: string) =>
    request<{ id: string; email: string }>("/auth/register", {
      ...json({ email, password }),
      authenticated: false,
    }),
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      ...json({ email, password }),
      authenticated: false,
    }),
  me: () => request<{ id: string; email: string }>("/auth/me"),

  projects: () => request<Page<Project>>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (input: CreateProjectInput) => request<Project>("/projects", json(input)),

  uploads: (projectId: string, form: FormData) =>
    request<Upload>(`/projects/${projectId}/upload`, { method: "POST", body: form }),
  extract: (projectId: string) =>
    request<ExtractResult>(`/projects/${projectId}/extract`, json({})),
  lines: (projectId: string) => request<LinesResponse>(`/projects/${projectId}/lines`),
  updateLine: (projectId: string, lineId: string, payload: Record<string, unknown>) =>
    request<Line>(`/projects/${projectId}/lines/${lineId}`, {
      ...json(payload),
      method: "PATCH",
    }),

  classify: (projectId: string) =>
    request<ClassifyResult>(`/projects/${projectId}/classify`, json({})),
  classifications: (projectId: string, params?: Record<string, string>) => {
    const query = params ? `?${new URLSearchParams(params)}` : "";
    return request<ClassificationsResponse>(`/projects/${projectId}/classifications${query}`);
  },
  classification: (projectId: string, id: string) =>
    request<ClassificationDetail>(`/projects/${projectId}/classifications/${id}`),
  review: (projectId: string, id: string, payload: Record<string, unknown>) =>
    request<Classification>(`/projects/${projectId}/classifications/${id}`, {
      ...json(payload),
      method: "PATCH",
    }),

  questions: (projectId: string) => request<QuestionsResponse>(`/projects/${projectId}/questions`),
  answer: (projectId: string, questionId: string, payload: Record<string, unknown>) =>
    request<{ question: Question; summary: ClassificationSummary }>(
      `/projects/${projectId}/questions/${questionId}/answer`,
      json(payload),
    ),
  activities: (projectId: string) =>
    request<{ items: BusinessActivity[] }>(`/projects/${projectId}/business-activities`),
  setActivity: (projectId: string, activityType: string, payload: Record<string, unknown>) =>
    request<BusinessActivity>(`/projects/${projectId}/business-activities/${activityType}`, {
      ...json(payload),
      method: "PUT",
    }),

  finalize: (projectId: string) =>
    request<FinalizeResult>(`/projects/${projectId}/finalize`, { method: "POST" }),
  reopen: (projectId: string) =>
    request<FinalizeResult>(`/projects/${projectId}/reopen`, { method: "POST" }),
  statement: (projectId: string) => request<Statement>(`/projects/${projectId}/statement`),
  impact: (projectId: string) => request<Impact>(`/projects/${projectId}/impact`),
  auditLogs: (projectId: string) => request<AuditLog>(`/projects/${projectId}/audit-logs`),

  /** The export is bytes, not JSON, so it bypasses `request`. */
  exportExcel: async (projectId: string, acknowledgeUnreconciled: boolean) => {
    const token = await readToken();
    const query = acknowledgeUnreconciled ? "?acknowledge_unreconciled=true" : "";
    const response = await fetch(
      `${apiBaseUrl()}/api/projects/${projectId}/export/excel${query}`,
      {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: "no-store",
      },
    );
    if (!response.ok) {
      let body: ProblemDocument | null = null;
      try {
        body = (await response.json()) as ProblemDocument;
      } catch {
        body = null;
      }
      throw new ApiError(
        body?.detail ?? "Export failed",
        response.status,
        problemCode(body),
        body,
      );
    }
    return {
      bytes: await response.arrayBuffer(),
      filename:
        /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ??
        "ifrs18-analysis.xlsx",
      unreconciled: response.headers.get("X-IFRS18-Reconciliation") === "FAILED",
    };
  },
};
