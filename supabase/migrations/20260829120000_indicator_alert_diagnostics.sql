alter table stockscout_unified_api.unified_alert_state
  add column diagnostics jsonb not null default '{}'::jsonb,
  add constraint unified_alert_state_diagnostics_object
    check (jsonb_typeof(diagnostics) = 'object');

notify pgrst,'reload schema';
