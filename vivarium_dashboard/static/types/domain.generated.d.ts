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
