// Generated from contracts/openapi.json. Run npm run types after contract changes.

export type Priority = "high" | "medium" | "low";

export type ProblemStatus = "unreviewed" | "assigned" | "in_progress" | "completed";

export type FileRole = "reports" | "assets" | "jobs_history";

export type ErrorDetail = {
  field?: string;
  message: string;
};

export type ErrorInfo = {
  code: string;
  message: string;
  retryable?: boolean;
  details?: Array<ErrorDetail>;
};

export type ErrorEnvelope = {
  error: ErrorInfo;
  requestId?: string;
};

export type Crew = {
  id: string;
  name: string;
  capabilities: Array<string>;
  description: string;
  assignedCount: number;
  inProgressCount: number;
};

export type Location = {
  label: string;
  detail: string | null;
};

export type PriorityFactor = {
  label: string;
  detail: string;
};

export type PriorityRecommendation = {
  level: Priority;
  explanation: string;
  factors: Array<PriorityFactor>;
};

export type CrewRecommendation = {
  crewId: string | null;
  reason: string;
  confidence: number | null;
};

export type Analysis = {
  state: "ready" | "needs_review" | "pending" | "failed";
  method: string;
  uncertainties: Array<string>;
};

export type Assignment = {
  id: string;
  crewId: string;
  assignedAt: string;
  startedAt: string | null;
  completedAt: string | null;
};

export type Decision = {
  priorityOverridden: boolean;
  priorityReason: string | null;
  crewOverridden: boolean;
  crewReason: string | null;
  reviewConfirmed: boolean;
  by: string;
  at: string;
};

export type AssetContext = {
  id: string;
  type: string;
  roadClass: string | null;
  ward: string | null;
  facility: string | null;
  distanceMeters: number | null;
  relationship: string;
};

export type JobHistoryContext = {
  id: string;
  completedAt: string | null;
  crewName: string;
  workType: string;
  notes: string | null;
  relationship: string;
};

export type SourceReport = {
  id: string;
  reference: string;
  receivedAt: string | null;
  channel: string;
  description: string;
  location: string | null;
};

export type Problem = {
  id: string;
  revision: number;
  title: string;
  description: string;
  location: Location | null;
  workType: string;
  firstReportedAt: string | null;
  latestReportedAt: string | null;
  reportCount: number;
  priority: Priority;
  priorityRecommendation: PriorityRecommendation;
  crewRecommendation: CrewRecommendation;
  analysis: Analysis;
  status: ProblemStatus;
  assignment: Assignment | null;
  decision: Decision | null;
};

export type ProblemDetail = {
  id: string;
  revision: number;
  title: string;
  description: string;
  location: Location | null;
  workType: string;
  firstReportedAt: string | null;
  latestReportedAt: string | null;
  reportCount: number;
  priority: Priority;
  priorityRecommendation: PriorityRecommendation;
  crewRecommendation: CrewRecommendation;
  analysis: Analysis;
  status: ProblemStatus;
  assignment: Assignment | null;
  decision: Decision | null;
  assets: Array<AssetContext>;
  recentJobs: Array<JobHistoryContext>;
};

export type ProblemPage = {
  items: Array<Problem>;
  total: number;
  page: number;
  pageSize: number;
};

export type SourceReportPage = {
  items: Array<SourceReport>;
  total: number;
  page: number;
  pageSize: number;
};

export type Overview = {
  sourceReportCount: number;
  distinctProblems: number;
  openProblems: number;
  highPriority: number;
  counts: {
  unreviewed: number;
  assigned: number;
  in_progress: number;
  completed: number;
};
  crews: Array<Crew>;
  analysisSummary: string | null;
  latestCompletedImportId: string | null;
};

export type ImportFile = {
  role: FileRole;
  name: string;
  status: "validating" | "ready" | "invalid";
  sizeBytes: number;
  rowCount: number | null;
  error: ErrorInfo | null;
};

export type ImportStage = {
  key: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  progress: number | null;
  message: string | null;
};

export type ImportWarning = {
  code: string;
  message: string;
  role: FileRole | null;
};

export type ImportSummary = {
  id: string;
  status: "draft" | "queued" | "processing" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
};

export type ImportBatch = {
  id: string;
  status: "draft" | "queued" | "processing" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
  revision: number;
  files: Array<ImportFile>;
  stages: Array<ImportStage>;
  warnings: Array<ImportWarning>;
  error: ErrorInfo | null;
};

export type ImportList = {
  items: Array<ImportSummary>;
};

export type CreateImportRequest = {

};

export type RunImportRequest = {
  revision: number;
};

export type PriorityDecisionRequest = {
  revision: number;
  priority: Priority;
  reason: string;
};

export type AssignRequest = {
  revision: number;
  crewId: string;
  reason: string;
  reviewConfirmed: true;
  location?: string;
};

export type StatusRequest = {
  revision: number;
  crewId: string;
  status: "in_progress" | "completed";
};
