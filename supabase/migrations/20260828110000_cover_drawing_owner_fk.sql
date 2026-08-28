create index if not exists unified_alerts_drawing_owner_fk_idx
  on stockscout_unified_api.unified_alerts(drawing_id,user_id);

