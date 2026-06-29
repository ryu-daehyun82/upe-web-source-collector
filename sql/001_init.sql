-- UPE Web Source Collector — 초기 스키마 (설계서 v2.0 §5 + v2.1 패치 P-5)
-- 적용: psql < sql/001_init.sql  (pgvector 확장은 web_patterns.embedding 용)
create extension if not exists vector;

-- 5.1 web_sources (+ v2.1: org_id, near_dup_key)
create table if not exists web_sources (
  id uuid primary key,
  org_id uuid,                                   -- v2.1 P-5 멀티테넌시
  url text not null,
  canonical_url text,
  domain text not null,
  source_type text not null,
  mime_type text,
  title text,
  discovered_by text,
  discovery_method text,
  crawl_status text not null,
  license_status text not null,
  robots_allowed boolean,
  terms_review_status text not null default 'not_reviewed',
  commercial_use_status text not null default 'unknown',
  pii_risk text not null default 'unknown',
  content_hash text,
  near_dup_key text,                             -- v2.1 P-5 근사중복(simhash/MinHash)
  snapshot_ref text,
  metadata_json jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_web_sources_domain on web_sources(domain);
create index if not exists idx_web_sources_status on web_sources(crawl_status, license_status);
create index if not exists idx_web_sources_hash on web_sources(content_hash);
create index if not exists idx_web_sources_orgnd on web_sources(org_id, near_dup_key);
create unique index if not exists ux_web_sources_url on web_sources(url);

-- 5.2 crawl_policies
create table if not exists crawl_policies (
  id uuid primary key,
  domain text not null,
  robots_url text,
  robots_snapshot_ref text,
  robots_checked_at timestamptz,
  allow_crawl boolean not null default false,
  allow_file_download boolean not null default false,
  allow_render boolean not null default false,
  max_depth integer not null default 0,
  max_pages_per_day integer not null default 100,
  crawl_delay_ms integer not null default 1000,
  include_patterns jsonb not null default '[]'::jsonb,
  exclude_patterns jsonb not null default '[]'::jsonb,
  review_status text not null default 'needs_review',
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists ux_crawl_policies_domain on crawl_policies(domain);

-- 5.3 crawl_jobs
create table if not exists crawl_jobs (
  id uuid primary key,
  source_id uuid not null references web_sources(id),
  url text not null,
  job_type text not null,
  status text not null,
  priority integer not null default 100,
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  idempotency_key text,                          -- v2.1 P-7 멱등
  scheduled_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  error_message text,
  trace_id text,
  job_config jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_crawl_jobs_status on crawl_jobs(status, priority, scheduled_at);
create index if not exists idx_crawl_jobs_source on crawl_jobs(source_id);
create unique index if not exists ux_crawl_jobs_idem on crawl_jobs(idempotency_key);

-- 5.4 crawl_snapshots
create table if not exists crawl_snapshots (
  id uuid primary key,
  source_id uuid not null references web_sources(id),
  crawl_job_id uuid references crawl_jobs(id),
  snapshot_type text not null,
  storage_ref text not null,
  content_hash text not null,
  byte_size bigint,
  content_type text,
  captured_at timestamptz not null default now(),
  retention_policy text not null default 'default',
  access_level text not null default 'restricted'
);
create index if not exists idx_crawl_snapshots_source on crawl_snapshots(source_id);

-- 5.5 web_patterns (+ v2.1: reuse 컬럼, org_id)
create table if not exists web_patterns (
  id uuid primary key,
  org_id uuid,                                   -- v2.1
  source_id uuid not null references web_sources(id),
  pattern_type text not null,
  abstraction_level text not null,
  original_reuse_risk text not null,
  reuse_subscores jsonb,                         -- v2.1 P-5 Risk Score 계약
  reuse_score numeric,
  reuse_hardrule text,
  recon_test_passed boolean,
  feature_json jsonb not null,
  embedding vector,
  license_status text not null,
  pii_status text not null,
  quality_score numeric,
  pattern_status text not null default 'built',
  version text not null default '1.0.0',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_web_patterns_source on web_patterns(source_id);
create index if not exists idx_web_patterns_type on web_patterns(pattern_type, pattern_status);
create index if not exists idx_web_patterns_risk on web_patterns(original_reuse_risk);

-- 5.6 web_audit_logs
create table if not exists web_audit_logs (
  id uuid primary key,
  actor_id text,
  actor_role text,
  action text not null,
  source_id uuid,
  job_id uuid,
  pattern_id uuid,
  before_json jsonb,
  after_json jsonb,
  reason text,
  trace_id text,
  created_at timestamptz not null default now()
);
create index if not exists idx_web_audit_source on web_audit_logs(source_id, created_at desc);
create index if not exists idx_web_audit_action on web_audit_logs(action, created_at desc);

-- 5.7 web_delete_requests
create table if not exists web_delete_requests (
  id uuid primary key,
  source_id uuid references web_sources(id),
  requester text,
  requester_contact text,
  request_type text not null,
  reason text,
  status text not null default 'received',
  received_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolution_note text
);

-- v2.1 P-5 신규: brand_risk (Reuse Risk Score brand_risk 입력)
create table if not exists brand_risk (
  domain text primary key,
  brand_risk numeric not null default 0.5,
  note text,
  updated_at timestamptz not null default now()
);
