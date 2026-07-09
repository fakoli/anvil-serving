create extension if not exists pgcrypto;

create table if not exists voices (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  language text,
  created_at timestamptz not null default now(),
  prefix bytea not null
);
