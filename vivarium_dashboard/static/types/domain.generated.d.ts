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
  svg: string;
}

export interface StudyChartsPayload {
  charts: ChartPayload[];
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
