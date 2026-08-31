// Type definitions mirroring the FastAPI backend response shapes.
// Field names are kept snake_case to match the wire format exactly.

export interface PortalCounts {
  contacts: number;
  companies: number;
  deals: number;
  tickets: number;
}

export interface PortalInfo {
  portal_id: number | string | null;
  // These four are emitted via account.get(...) with no fallback on the
  // backend, so they arrive as null when account-info omits the key.
  hub_domain: string | null;
  hub_name: string;
  timezone: string | null;
  currency: string | null;
  created_at: string | null;
  counts: PortalCounts;
}

export interface ListEnvelope<T> {
  results: T[];
  total: number;
  next_after: string | null | undefined;
}

export interface ContactRow {
  id: string;
  firstname: string;
  lastname: string;
  email: string;
  phone: string;
  company: string;
  lifecycle: string;
  lead_status: string;
  created_at: string;
}

export interface CompanyRow {
  id: string;
  name: string;
  domain: string;
  industry: string;
  city: string;
  country: string;
  revenue: string;
  employees: string;
  created_at: string;
}

export interface DealRow {
  id: string;
  name: string;
  amount: string;
  stage: string;
  pipeline: string;
  close_date: string;
  probability: string;
  deal_type: string;
  created_at: string;
}

export interface TicketRow {
  id: string;
  subject: string;
  priority: string;
  stage: string;
  created_at: string;
  updated_at: string;
}

export interface PipelineStage {
  id: string;
  label: string;
  probability: string;
  display_order: number;
}

export interface Pipeline {
  id: string;
  label: string;
  stages: PipelineStage[];
}

export interface PipelinesResponse {
  pipelines: Pipeline[];
}

export interface CacheMeta {
  cached: boolean;
  computed_at: string;
  age_seconds: number;
  ttl_seconds: number;
}

export interface OrphanCompany {
  id: string;
  name: string;
  domain: string;
}

export interface CompanyClusterMember {
  id: string;
  name: string;
  domain: string;
}

export interface DuplicateCompanyCluster {
  type: "domain" | "name";
  key: string;
  count: number;
  companies: CompanyClusterMember[];
}

export interface CrmHealth {
  scanned_companies: number;
  fc_deal_count: number;
  edge_company_count: number;
  orphan_count: number;
  duplicate_cluster_count: number;
  orphans: OrphanCompany[];
  duplicate_clusters: DuplicateCompanyCluster[];
  orphan_ids: string[];
  duplicate_ids: string[];
  capped: boolean;
  cache?: CacheMeta;
}

export interface MissingStat {
  count: number;
  pct: number;
}

export interface MissingPrevalence {
  email: MissingStat;
  phone: MissingStat;
  name: MissingStat;
  company: MissingStat;
}

export interface HealthContactRow {
  id: string;
  name: string;
  email: string;
  phone: string;
  lifecycle: string;
  lead_status: string;
  company_count: number;
  created_at: string;
  missing?: string[];
}

export interface ContactClusterMember {
  id: string;
  name: string;
  email: string;
  lifecycle: string;
}

export interface DuplicateContactCluster {
  type: "email" | "name";
  key: string;
  count: number;
  contacts: ContactClusterMember[];
}

export interface MultiCompanyContact {
  id: string;
  name: string;
  email: string;
  company_count: number;
}

export interface ContactHealth {
  scanned_contacts: number;
  nql_count: number;
  mql_count: number;
  borderline_count: number;
  duplicate_cluster_count: number;
  multi_company_count: number;
  missing_prevalence: MissingPrevalence;
  nql_contacts: HealthContactRow[];
  mql_contacts: HealthContactRow[];
  borderline_contacts: HealthContactRow[];
  duplicate_clusters: DuplicateContactCluster[];
  multi_company_contacts: MultiCompanyContact[];
  nql_ids: string[];
  duplicate_ids: string[];
  multi_company_ids: string[];
  capped: boolean;
  cache?: CacheMeta;
}

export interface SuppressionResult {
  success: boolean;
  list_id: number | string;
  list_name: string;
  added: number;
  errors: number;
  url: string;
}

export interface ScoreCriterion {
  id: string;
  label: string;
  met: boolean;
  points: number;
  max_points: number;
}

export interface ScoreRow {
  id: string;
  score: number;
  band: string;
}

export interface ScoredContactRow extends ScoreRow {
  name: string;
  email: string;
  max_score: number;
  criteria: ScoreCriterion[];
}

export interface ScoringRule {
  id: string;
  label: string;
  feature: string;
  kind: string;
  points: number;
  value: string[] | number | null;
}

export interface ContactScoresResponse {
  object_type: string;
  scanned: number;
  capped: boolean;
  max_score: number;
  average_score: number;
  band_counts: Record<string, number>;
  scores: ScoreRow[];
  lowest: ScoredContactRow[];
  highest: ScoredContactRow[];
  criteria: ScoringRule[];
  cache?: CacheMeta;
}
