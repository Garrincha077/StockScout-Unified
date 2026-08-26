create schema if not exists stockscout_unified_api;

revoke all on schema stockscout_unified_api from public, anon, authenticated;
grant usage on schema stockscout_unified_api to authenticated;

create table stockscout_unified_api.owner_allowlist (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table stockscout_unified_api.unified_watchlist_items (
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 80),
  ticker text not null check (ticker ~ '^[A-Z0-9._-]{1,20}$'),
  mode text not null check (mode in ('bottom-fishing','next','ryan-original')),
  price_basis text not null check (price_basis in ('split_only','split_div')),
  created_at timestamptz not null default now(),
  primary key (user_id,name,ticker,mode,price_basis)
);

create table stockscout_unified_api.unified_saved_screens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 80),
  mode text not null check (mode in ('bottom-fishing','next','ryan-original')),
  price_basis text not null check (price_basis in ('split_only','split_div')),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id,name,mode,price_basis)
);

create table stockscout_unified_api.unified_drawings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker ~ '^[A-Z0-9._-]{1,20}$'),
  interval text not null check (char_length(interval) between 1 and 20),
  mode text not null check (mode in ('bottom-fishing','next','ryan-original')),
  price_basis text not null check (price_basis in ('split_only','split_div')),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table stockscout_unified_api.unified_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 120),
  ticker text check (ticker is null or ticker ~ '^[A-Z0-9._-]{1,20}$'),
  mode text not null check (mode in ('bottom-fishing','next','ryan-original')),
  price_basis text not null check (price_basis in ('split_only','split_div')),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table stockscout_unified_api.unified_alert_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  alert_id uuid not null references stockscout_unified_api.unified_alerts(id) on delete cascade,
  run_id text not null,
  mode text not null check (mode in ('bottom-fishing','next','ryan-original')),
  price_basis text not null check (price_basis in ('split_only','split_div')),
  event_key text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  triggered_at timestamptz not null default now(),
  unique (user_id,event_key)
);

create table stockscout_unified_api.unified_delivery_state (
  user_id uuid not null references auth.users(id) on delete cascade,
  channel text not null check (channel in ('telegram')),
  series text not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  total_parts integer not null check (total_parts > 0),
  last_successful_part integer not null default 0 check (last_successful_part >= 0),
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (user_id,channel,series)
);

create index unified_drawings_lookup_idx on stockscout_unified_api.unified_drawings (user_id,mode,price_basis,ticker,updated_at desc);
create index unified_alerts_enabled_idx on stockscout_unified_api.unified_alerts (user_id,enabled,mode,price_basis) where enabled;
create index unified_alert_events_recent_idx on stockscout_unified_api.unified_alert_events (user_id,triggered_at desc);

create function stockscout_unified_api.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger unified_saved_screens_updated before update on stockscout_unified_api.unified_saved_screens for each row execute function stockscout_unified_api.set_updated_at();
create trigger unified_drawings_updated before update on stockscout_unified_api.unified_drawings for each row execute function stockscout_unified_api.set_updated_at();
create trigger unified_alerts_updated before update on stockscout_unified_api.unified_alerts for each row execute function stockscout_unified_api.set_updated_at();
create trigger unified_delivery_updated before update on stockscout_unified_api.unified_delivery_state for each row execute function stockscout_unified_api.set_updated_at();

alter table stockscout_unified_api.owner_allowlist enable row level security;
alter table stockscout_unified_api.unified_watchlist_items enable row level security;
alter table stockscout_unified_api.unified_saved_screens enable row level security;
alter table stockscout_unified_api.unified_drawings enable row level security;
alter table stockscout_unified_api.unified_alerts enable row level security;
alter table stockscout_unified_api.unified_alert_events enable row level security;
alter table stockscout_unified_api.unified_delivery_state enable row level security;

create policy owner_allowlist_read_self on stockscout_unified_api.owner_allowlist
for select to authenticated using ((select auth.uid()) = user_id);

create policy owner_watchlist_self on stockscout_unified_api.unified_watchlist_items
for all to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())))
with check ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create policy owner_saved_screens_self on stockscout_unified_api.unified_saved_screens
for all to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())))
with check ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create policy owner_drawings_self on stockscout_unified_api.unified_drawings
for all to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())))
with check ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create policy owner_alerts_self on stockscout_unified_api.unified_alerts
for all to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())))
with check ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create policy owner_alert_events_read on stockscout_unified_api.unified_alert_events
for select to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create policy owner_delivery_state_read on stockscout_unified_api.unified_delivery_state
for select to authenticated
using ((select auth.uid()) = user_id and exists (select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())));

create function stockscout_unified_api.unified_set_watchlist_ticker(
  p_name text,
  p_ticker text,
  p_mode text,
  p_price_basis text,
  p_present boolean
)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if p_present then
    insert into stockscout_unified_api.unified_watchlist_items (user_id,name,ticker,mode,price_basis)
    values ((select auth.uid()),p_name,upper(p_ticker),p_mode,p_price_basis)
    on conflict do nothing;
  else
    delete from stockscout_unified_api.unified_watchlist_items
    where user_id = (select auth.uid()) and name = p_name and ticker = upper(p_ticker)
      and mode = p_mode and price_basis = p_price_basis;
  end if;
end;
$$;

revoke all on all tables in schema stockscout_unified_api from public, anon;
revoke all on all functions in schema stockscout_unified_api from public, anon, authenticated;
grant select on stockscout_unified_api.owner_allowlist to authenticated;
grant select,insert,update,delete on stockscout_unified_api.unified_watchlist_items to authenticated;
grant select,insert,update,delete on stockscout_unified_api.unified_saved_screens to authenticated;
grant select,insert,update,delete on stockscout_unified_api.unified_drawings to authenticated;
grant select,insert,update,delete on stockscout_unified_api.unified_alerts to authenticated;
grant select on stockscout_unified_api.unified_alert_events to authenticated;
grant select on stockscout_unified_api.unified_delivery_state to authenticated;
grant execute on function stockscout_unified_api.unified_set_watchlist_ticker(text,text,text,text,boolean) to authenticated;
