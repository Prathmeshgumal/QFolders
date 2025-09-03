-- Enable pgcrypto for gen_random_uuid() if not already enabled
create extension if not exists pgcrypto;

-- Folders
create table if not exists public.folders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);

-- Questions
create table if not exists public.questions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  folder_id uuid not null references public.folders(id) on delete cascade,
  title text not null,
  description text null,
  notes text null,
  links jsonb null,
  code text null,
  created_at timestamptz not null default now()
);

alter table public.folders enable row level security;
alter table public.questions enable row level security;

-- Policies: Only the owner can CRUD

-- Folders
drop policy if exists "folders_select_own" on public.folders;
create policy "folders_select_own" on public.folders
  for select using (auth.uid() = user_id);

drop policy if exists "folders_insert_self" on public.folders;
create policy "folders_insert_self" on public.folders
  for insert with check (auth.uid() = user_id);

drop policy if exists "folders_update_own" on public.folders;
create policy "folders_update_own" on public.folders
  for update using (auth.uid() = user_id);

drop policy if exists "folders_delete_own" on public.folders;
create policy "folders_delete_own" on public.folders
  for delete using (auth.uid() = user_id);

-- Questions
drop policy if exists "questions_select_own" on public.questions;
create policy "questions_select_own" on public.questions
  for select using (auth.uid() = user_id);

drop policy if exists "questions_insert_self" on public.questions;
create policy "questions_insert_self" on public.questions
  for insert with check (auth.uid() = user_id);

drop policy if exists "questions_update_own" on public.questions;
create policy "questions_update_own" on public.questions
  for update using (auth.uid() = user_id);

drop policy if exists "questions_delete_own" on public.questions;
create policy "questions_delete_own" on public.questions
  for delete using (auth.uid() = user_id);

-- Helpful indexes
create index if not exists idx_folders_user on public.folders(user_id, created_at desc);
create index if not exists idx_questions_folder on public.questions(folder_id, created_at desc);
create index if not exists idx_questions_user on public.questions(user_id, created_at desc);
