grant usage on schema stockscout_unified_api to service_role;
grant select on stockscout_unified_api.owner_allowlist to service_role;
grant select on stockscout_unified_api.unified_alerts to service_role;
grant select,insert on stockscout_unified_api.unified_alert_events to service_role;
grant select,insert,update on stockscout_unified_api.unified_delivery_state to service_role;
grant usage,select on all sequences in schema stockscout_unified_api to service_role;
