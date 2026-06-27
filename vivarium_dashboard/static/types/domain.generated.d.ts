// AUTO-GENERATED from vivarium_dashboard/lib/models.py — do not edit by hand.
// Regenerate: python -m vivarium_dashboard.lib.generate_ts

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
  spec_id: string;
  sim_name: string | null;
  label: string | null;
  status: string;
  n_steps: number | null;
  progress_step: number | null;
  started_at: number;
  completed_at: number | null;
  db_path: string;
  store_path: string | null;
  emitter: EmitterKind | null;
  studies: StudyRef[];
  study_slug: string | null;
  investigation_slug: string | null;
  remote_origin: RemoteOrigin | null;
}

export interface SimulationsPayload {
  simulations: SimRow[];
  current: string | null;
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
}

export interface StudyChartsPayload {
  study: string;
  schema_version: any | null;
  charts: ChartPayload[];
  db_exists: boolean;
  static_count: number;
  live_count: number;
}

export interface DashConfig {
  mode: string;
  basePath: string | null;
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
  studies: string[];
  lifecycle: any;
  current: boolean | null;
  error: string | null;
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

export interface PtoolsStudy {
  study: string;
  n_tsvs: number;
}

export interface PtoolsInfo {
  configured: boolean;
  studies: PtoolsStudy[];
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
  ptools: PtoolsInfo;
  report_cards: ReportCard[];
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

export interface BranchStaleness {
  branch: string;
  base: string;
  behind_ref: string;
  commits_behind: number;
  stale_threshold: number;
  stale: boolean;
}

export interface DirtyFile {
  status: string;
  path: string;
}

export interface DirtyStatus {
  count: number;
  files: DirtyFile[];
}

export interface BranchCommit {
  sha: string;
  subject: string;
  date: string;
}

export interface BranchInfo {
  name: string;
  last_commit: BranchCommit;
  ahead_of_main: number;
}

export interface BranchesPayload {
  branches: BranchInfo[];
  current: string | null;
}

export interface BranchDiff {
  branch: string;
  log: string;
  diff_stat: string;
}

export interface PendingEntries {
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

export interface StudyRigor {
}

export interface InvestigationRigor {
}

export interface StudyDetail {
}

export interface ExplorerRuns {
}

export interface ExplorerObservables {
}

export interface ExplorerSeries {
}

export interface ExplorerFlux {
}

export interface ExplorerVector {
}

export interface ExplorerProteinBreakdown {
}

export interface ReportLint {
}

export interface NeedsAttention {
}

export interface InputsPayload {
}

export interface IsetDetail {
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
  composite_view: string;
  ptools_server_url: string;
  ptools_omics_url_template: string;
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

export interface PtoolsLaunch {
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

export interface FeedbackImportResponse {
  ok: boolean;
  path: string;
  n_entries: number;
}

export interface VisualizationAcceptResponse {
  ok: boolean;
}
