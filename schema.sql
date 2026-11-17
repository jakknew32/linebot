-- Supabase schema for LINE bot reader
create table if not exists public.messages (
  id bigserial primary key,
  received_at timestamptz not null default now(),
  user_id text,
  group_id text,
  room_id text,
  event_type text,
  message_type text,
  text text,
  raw_json jsonb,
  store_code text,
  store_name text,
  branch text,
  delivery_date text,
  items jsonb,
  phone text,
  note text
);

create table if not exists public.users (
  user_id text primary key,
  display_name text,
  picture_url text,
  status_message text,
  updated_at timestamptz not null default now()
);

create index if not exists idx_messages_user on public.messages(user_id);
create index if not exists idx_messages_received on public.messages(received_at desc);
create index if not exists idx_messages_store on public.messages(store_code);

alter table public.messages enable row level security;
alter table public.users enable row level security;
create policy "allow all for anon" on public.messages for all using (true) with check (true);
create policy "allow all for anon" on public.users for all using (true) with check (true);

-- If table already exists, run:
-- alter table public.messages add column if not exists store_code text;
-- alter table public.messages add column if not exists store_name text;
-- alter table public.messages add column if not exists branch text;
-- alter table public.messages add column if not exists delivery_date text;
-- alter table public.messages add column if not exists items jsonb;
-- alter table public.messages add column if not exists phone text;
-- alter table public.messages add column if not exists note text;
