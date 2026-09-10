// AUTO-GENERATED from vivarium_workbench/lib/models.py — do not edit by hand.
// Regenerate: python -m vivarium_workbench.lib.generate_ts

export type EmitterKind = 'xarray' | 'parquet' | 'sqlite';

export type RemoteJobStatus = 'unknown' | 'waiting' | 'pending' | 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';

export interface RemoteOrigin {
  deployment: string;
  simulation_id: number;
  experiment_id: string | null;
  backend: string | null;
  s3_uri: string | null;
}

export interface StudyRef {
  slug: string;
  label: string | null;
}

export interface SimRow {
  run_id: string;
  spec_id: string | null;
  composite_registered: boolean | null;
  config: Record<string, any> | null;
  sim_name: string | null;
  label: string | null;
  status: string;
  n_steps: number | null;
  progress_step: number | null;
  started_at: number | null;
  completed_at: number | null;
  db_path: string | null;
  store_path: string | null;
  emitter: string | null;
  emitter_type: string | null;
  studies: (StudyRef | string)[];
  study_slug: string | null;
  investigation_slug: string | null;
  remote_origin: RemoteOrigin | null;
  source_ref: Record<string, any> | null;
  capabilities: string[];
  matched_tools: Record<string, any>[];
}

export interface SimulationsPayload {
  simulations: SimRow[];
  current: string | null;
  total: number | null;
  offset: number;
  limit: number | null;
}

export interface RemoteRunStep {
  name: string;
  status: string;
  message: string;
}

export interface RemoteRunJob {
  job_id: string;
  study: string;
  status: RemoteJobStatus;
  steps: RemoteRunStep[];
  run_id: string | null;
  simulation_id: number | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ChartPayload {
  key: string;
  title: string;
  caption: string;
  svg: string | null;
  img: string | null;
  source: string | null;
  media: string | null;
  freshness: string | null;
  simulations: string | null;
  interpretation: string | null;
  data_source: string | null;
  run_id: string | null;
}

export interface StudyChartsPayload {
  study: string;
  schema_version: any | null;
  charts: ChartPayload[];
  db_exists: boolean;
  data_store: string | null;
  static_count: number;
  live_count: number;
}

export interface InvestigationSummary {
  name: string;
  title: string | null;
  status: string | null;
  effective_status: string | null;
  description: string | null;
  question: string | null;
  hypothesis: string | null;
  n_studies: number | null;
  n_figures: number | null;
  studies: string[];
  lifecycle: any;
  current: boolean | null;
  run_command: string | null;
  error: string | null;
  origin_repo: string | null;
  read_only: boolean | null;
}

export interface InvestigationSummariesPayload {
  investigations: InvestigationSummary[];
}

export interface DataSource {
  key: string;
  path: string;
  category: string;
  kind: string;
  size_bytes: number;
  url: string;
}

export interface DataSourcesPayload {
  label: string | null;
  sources: DataSource[];
  error: string | null;
}

export interface BibEntry {
  key: string;
  type: string | null;
  title: string | null;
  author: string | null;
  journal: string | null;
  year: string | null;
  doi: string | null;
  url: string | null;
  note: string | null;
}

export interface ReferencesBibPayload {
  entries: BibEntry[];
}

export interface SavedViz {
  study: string;
  name: string;
  pack_url: string;
  meta_url: string | null;
  n_placed: number | null;
  created: number | null;
  viewer_url: string | null;
}

export interface ReportCard {
  study: string | null;
  name: string;
  url: string;
  verdict: string | null;
  created: number | null;
}

export interface SavedVisualizationsPayload {
  parsimony_available: boolean;
  saved: SavedViz[];
  report_cards: ReportCard[];
}

export interface AnalysisToolsPayload {
  tools: Record<string, any>[];
}

export interface GitStatus {
  upstream_repo: string | null;
  branch: string | null;
  push_state: string;
  ahead: number;
  behind: number;
  branch_url: string | null;
  repo_url: string | null;
  pr_number: number | null;
  pr_url: string | null;
  base: string;
  ahead_of_base: number;
  dirty_count: number;
  compare_url: string | null;
  pr_state: string | null;
  gh_available: boolean;
  has_active_workstream: boolean;
}

export interface WorkStatusInactive {
  active: false;
}

export interface WorkStatusActive {
  active: true;
  branch: string | null;
  base: string | null;
  commits_ahead: number | null;
  commits_behind: number | null;
  behind_ref: string | null;
  stale: boolean | null;
  stale_threshold: number | null;
  unpushed: number | null;
  pushed: boolean | null;
  has_origin: boolean | null;
  gh_available: boolean | null;
  pr_number: number | null;
  pr_url: string | null;
}

export interface DirtyFile {
  status: string;
  path: string;
}

export interface DirtyStatus {
  count: number;
  files: DirtyFile[];
}

export interface GenerationSummary {
  generation_id: string;
  git_sha: string | null;
  param_set_hash: string | null;
  created_at: string | null;
  label: string | null;
  n_runs: number;
}

export interface Generation {
  generation: GenerationSummary | null;
}

export interface WorkCompositeDiffEntry {
  path: string;
  lines_added: number;
  lines_removed: number;
  category: string;
}

export interface WorkCompositeDiff {
  base: string;
  branch: string;
  changes: WorkCompositeDiffEntry[];
  error: string | null;
}

export interface VizHtmlFile {
  name: string;
  html_path: string;
}

export interface InvestigationVizHtmlPayload {
  viz_files: VizHtmlFile[];
  error: string | null;
}

export interface InvestigationCompositeEntry {
  name: string;
  source: string;
  params: any;
}

export interface InvestigationCompositesPayload {
  composites: InvestigationCompositeEntry[];
}

export interface InvestigationCompositeDocPayload {
  state: any;
}

export interface InvestigationStateTree {
  nodes: any[];
}

export interface InvestigationHypothesesPayload {
  hypotheses: any[];
  investigation: string;
}

export interface InvestigationRigor {
}

export interface StudyDetail {
}

export interface ReportLint {
}

export interface NeedsAttention {
}

export interface InputsPayload {
}

export interface IsetDetail {
}

export interface BandProvenanceMissing {
}

export interface BandProvenanceResult {
}

export interface CitationGaps {
}

export interface ExpertSearchResult {
}

export interface StudyFindingsPopulateResult {
  study: string | null;
  filled: number;
  skipped: number;
}

export interface StudyVerifyResult {
  study: string | null;
  study_yaml: string | null;
  findings: Record<string, any>[] | null;
  summary: Record<string, any> | null;
}

export interface StudyNarrativeCommandResult {
  study: string | null;
  subcommand: string | null;
  message: string | null;
  dry_run: boolean;
}

export interface StudyFindingsResult {
  study: string | null;
  proposed: number;
  appended: number;
  skipped_existing: number;
  cited_bib_keys: string[];
  unknown_bib_keys: string[];
  dry_run: boolean;
  wrote: boolean;
  wrote_path: string | null;
}

export interface StudyReadoutMigrationStatusResult {
  study: string;
  canonical: Record<string, any>[];
  migratable: Record<string, any>[];
  needs_human: Record<string, any>[];
}

export interface FeedbackRecordActionResult {
  recorded: boolean;
  path: string | null;
  kind: string | null;
}

export interface ISetCloseResult {
  slug: string | null;
  branch: string | null;
  contributors: Record<string, any>[];
  actions: Record<string, any>[];
  pr_url: string | null;
  dry_run: boolean;
}

export interface ObservablesPayload {
}

export interface StudyObservableCheck {
}

export interface LinkageIndex {
}

export interface CompositeState {
}

export interface FrameworkMetrics {
  metrics: any;
  n_investigations: number;
  n_studies: number;
}

export interface GithubRepo {
  repo: string | null;
}

export interface UiConfig {
  readonly: boolean;
  composite_view: string;
  auto_results: boolean;
}

export interface WorkspaceHome {
}

export interface CompositeRunsList {
}

export interface CompositeRunTrajectory {
}

export interface CompositeRunState {
}

export interface CompositeRunStatus {
}

export interface StudyBigraphPaths {
}

export interface VisualizationStatus {
}

export interface VisualizationInstances {
}

export interface SourceBuilds {
}

export interface WorkspacesList {
}

export interface SystemDepsCheck {
}

export interface JobStatusPayload {
}

export interface SourceSwitchSource {
  path: string;
  name: string | null;
}

export interface SourceSwitchResponse {
  ok: boolean;
  source: SourceSwitchSource;
}

export interface BuildRemoteResponse {
  ok: boolean;
  simulator_id: number | null;
  repo: string;
  branch: string;
  commit: string;
}

export interface RemoteRunStartResponse {
  job_id: string;
}

export interface AuthPayload {
}

export interface BranchPushResponse {
  ok: boolean;
  pushed: boolean;
  commit: string;
  branch: string;
}

export interface DirtyCommitAllResponse {
  commit_sha: string;
  message: string;
  paths: string[];
}

export interface WorkStartResponse {
  ok: boolean;
  branch: string;
  base: string;
}

export interface WorkPushResponse {
  ok: boolean;
  branch: string;
  log: string;
}

export interface WorkEndResponse {
  ok: boolean;
}

export interface WorkAttachReportResponse {
  ok: boolean;
  path: string;
  branch: string;
}

export interface WorkCreatePrResponse {
  ok: boolean;
  pr_url: string;
  pr_number: number | null;
}

export interface WorkLinkBranchResponse {
  ok: boolean;
  branch: string;
  branch_url: string;
}

export interface WorkspacesOkResponse {
  ok: boolean;
}

export interface WorkspaceEntry {
}

export interface RenderResponse {
  ok: boolean;
}

export interface VisualizationAcceptResponse {
  ok: boolean;
}
