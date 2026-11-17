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
  raw_json jsonb
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

-- Disable RLS or add policies for service role usage
alter table public.messages enable row level security;
alter table public.users enable row level security;
create policy "allow all for anon" on public.messages for all using (true) with check (true);
create policy "allow all for anon" on public.users for all using (true) with check (true);
