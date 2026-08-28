alter table stockscout_unified_api.unified_drawings
  add constraint unified_drawings_id_user_unique unique (id,user_id);

alter table stockscout_unified_api.unified_alerts
  add column drawing_id uuid,
  add constraint unified_alerts_id_user_unique unique (id,user_id),
  add constraint unified_alerts_drawing_owner_fk foreign key (drawing_id,user_id)
    references stockscout_unified_api.unified_drawings(id,user_id) on delete cascade;

create index unified_alerts_drawing_idx
  on stockscout_unified_api.unified_alerts(user_id,drawing_id)
  where drawing_id is not null;

create table stockscout_unified_api.unified_alert_state (
  alert_id uuid not null,
  user_id uuid not null,
  config_version timestamptz not null,
  armed boolean not null default true,
  last_condition boolean not null default false,
  last_relation text,
  last_run_id text,
  last_session_date date,
  previous_price double precision,
  current_price double precision,
  previous_level double precision,
  current_level double precision,
  evaluated_at timestamptz,
  error text,
  updated_at timestamptz not null default now(),
  primary key(alert_id,user_id),
  constraint unified_alert_state_alert_owner_fk foreign key(alert_id,user_id)
    references stockscout_unified_api.unified_alerts(id,user_id) on delete cascade
);

alter table stockscout_unified_api.unified_alert_state enable row level security;

create policy owner_alert_state_read on stockscout_unified_api.unified_alert_state
for select to authenticated
using ((select auth.uid()) is not null and (select auth.uid()) = user_id and exists (
  select 1 from stockscout_unified_api.owner_allowlist a where a.user_id = (select auth.uid())
));

create function stockscout_unified_api.reset_drawing_alert_state()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from stockscout_unified_api.unified_alert_state
  where user_id = new.user_id and alert_id in (
    select id from stockscout_unified_api.unified_alerts
    where user_id = new.user_id and drawing_id = new.id
  );
  return new;
end;
$$;

create trigger unified_drawing_alert_state_reset
after update of payload on stockscout_unified_api.unified_drawings
for each row execute function stockscout_unified_api.reset_drawing_alert_state();

create function stockscout_unified_api.reset_alert_state()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from stockscout_unified_api.unified_alert_state
  where user_id = new.user_id and alert_id = new.id;
  return new;
end;
$$;

create trigger unified_alert_state_reset
after update of payload,drawing_id,enabled on stockscout_unified_api.unified_alerts
for each row execute function stockscout_unified_api.reset_alert_state();

revoke all on stockscout_unified_api.unified_alert_state from public,anon,authenticated;
grant select on stockscout_unified_api.unified_alert_state to authenticated;
grant select on stockscout_unified_api.unified_drawings to service_role;
grant select,insert,update on stockscout_unified_api.unified_alert_state to service_role;
revoke all on function stockscout_unified_api.reset_drawing_alert_state() from public,anon,authenticated;
revoke all on function stockscout_unified_api.reset_alert_state() from public,anon,authenticated;

notify pgrst,'reload schema';
