-- Supabase schema for LINE bot reader

-- 1. Base tables
create table if not exists public.messages (
  id bigserial primary key,
  received_at timestamptz not null default now(),
  user_id text,
  group_id text,
  room_id text,
  event_type text,
  message_type text,
  text text,
  raw_json jsonb
);

create table if not exists public.users (
  user_id text primary key,
  display_name text,
  picture_url text,
  status_message text,
  updated_at timestamptz not null default now()
);

-- 2. Add parsed order columns (safe for existing tables)
alter table public.messages add column if not exists store_code text;
alter table public.messages add column if not exists store_name text;
alter table public.messages add column if not exists branch text;
alter table public.messages add column if not exists delivery_date text;
alter table public.messages add column if not exists items jsonb;
alter table public.messages add column if not exists phone text;
alter table public.messages add column if not exists note text;

-- 3. Indexes
create index if not exists idx_messages_user on public.messages(user_id);
create index if not exists idx_messages_received on public.messages(received_at desc);
create index if not exists idx_messages_store on public.messages(store_code);

-- 4. RLS + policies (idempotent)
alter table public.messages enable row level security;
alter table public.users enable row level security;

do $$
begin
  if not exists (select 1 from pg_policies where tablename='messages' and policyname='allow all for anon') then
    create policy "allow all for anon" on public.messages for all using (true) with check (true);
  end if;
  if not exists (select 1 from pg_policies where tablename='users' and policyname='allow all for anon') then
    create policy "allow all for anon" on public.users for all using (true) with check (true);
  end if;
end $$;
