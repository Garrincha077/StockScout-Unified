begin;
select plan(27);

insert into auth.users (id,email) values
  ('11111111-1111-1111-1111-111111111111','owner@example.test'),
  ('22222222-2222-2222-2222-222222222222','other@example.test');
insert into stockscout_unified_api.owner_allowlist(user_id)
values('11111111-1111-1111-1111-111111111111');
insert into stockscout_unified_api.unified_drawings(id,user_id,ticker,interval,mode,price_basis,payload)
values('33333333-3333-3333-3333-333333333333','22222222-2222-2222-2222-222222222222','BBB','daily','next','split_div','{"version":2,"type":"horizontal","points":[{"time":"2026-08-20","price":10},{"time":"2026-08-21","price":10}]}'::jsonb);

select ok(not has_schema_privilege('anon','stockscout_unified_api','usage'),'anon cannot use the owner schema');
select ok(not has_table_privilege('anon','stockscout_unified_api.unified_drawings','select'),'anon cannot read drawings');
select ok(not has_table_privilege('anon','stockscout_unified_api.unified_alerts','insert'),'anon cannot create alerts');
select ok(not has_table_privilege('anon','stockscout_unified_api.unified_alert_state','select'),'anon cannot read alert runtime state');
select ok(has_schema_privilege('authenticated','stockscout_unified_api','usage'),'authenticated role can use the owner schema');
select ok(has_table_privilege('authenticated','stockscout_unified_api.unified_drawings','select,insert,update,delete'),'authenticated role has drawing operations gated by RLS');
select ok(has_table_privilege('authenticated','stockscout_unified_api.unified_alerts','select,insert,update,delete'),'authenticated role has alert operations gated by RLS');
select ok(has_table_privilege('authenticated','stockscout_unified_api.unified_alert_state','select'),'owner can read runtime alert state');
select ok(not has_table_privilege('authenticated','stockscout_unified_api.unified_alert_state','insert,update,delete'),'frontend cannot mutate server-controlled alert state');
select ok(has_table_privilege('service_role','stockscout_unified_api.unified_drawings','select'),'evaluator can resolve linked drawing geometry');
select ok(has_table_privilege('service_role','stockscout_unified_api.unified_alert_state','select,insert,update'),'evaluator can maintain runtime alert state');
select ok(not has_table_privilege('service_role','stockscout_unified_api.unified_drawings','insert,update,delete'),'evaluator cannot mutate owner drawings');

set local role authenticated;
select set_config('request.jwt.claims','{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}',true);
select lives_ok($$insert into stockscout_unified_api.unified_drawings(user_id,ticker,interval,mode,price_basis,payload) values('11111111-1111-1111-1111-111111111111','AAA','daily','next','split_div','{"version":1,"type":"horizontal","points":[]}'::jsonb)$$,'allowlisted owner creates own drawing');
select lives_ok($$insert into stockscout_unified_api.unified_alerts(user_id,name,ticker,mode,price_basis,payload) values('11111111-1111-1111-1111-111111111111','AAA above 10','AAA','next','split_div','{"version":1,"kind":"price","operator":"above","price":10}'::jsonb)$$,'allowlisted owner creates own alert');
select lives_ok($$insert into stockscout_unified_api.unified_alerts(user_id,name,ticker,mode,price_basis,drawing_id,payload) select '11111111-1111-1111-1111-111111111111','AAA linked drawing','AAA','next','split_div',id,jsonb_build_object('version',2,'kind','drawing','drawingId',id,'condition','touch','target',jsonb_build_object('kind','line'),'evaluationInterval','daily','rearm','after_clear') from stockscout_unified_api.unified_drawings limit 1$$,'owner links an alert only to an owned drawing');
select throws_ok($$insert into stockscout_unified_api.unified_alerts(user_id,name,ticker,mode,price_basis,drawing_id,payload) values('11111111-1111-1111-1111-111111111111','cross-owner drawing','BBB','next','split_div','33333333-3333-3333-3333-333333333333','{"version":2,"kind":"drawing","drawingId":"33333333-3333-3333-3333-333333333333"}'::jsonb)$$,'23503',null,'composite foreign key blocks cross-owner drawing links');
select is((select count(*)::integer from stockscout_unified_api.unified_drawings),1,'owner reads own drawing');
select lives_ok($$update stockscout_unified_api.unified_alerts set enabled=false where name='AAA above 10'$$,'owner updates own alert');

reset role;
set local role authenticated;
select set_config('request.jwt.claims','{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}',true);
select is((select count(*)::integer from stockscout_unified_api.unified_drawings),0,'non-owner cannot read owner drawings');
select is((select count(*)::integer from stockscout_unified_api.unified_alerts),0,'non-owner cannot read owner alerts');
select throws_ok($$insert into stockscout_unified_api.unified_drawings(user_id,ticker,interval,mode,price_basis,payload) values('22222222-2222-2222-2222-222222222222','BBB','daily','next','split_div','{}'::jsonb)$$,'42501',null,'non-allowlisted user cannot create drawing');
select throws_ok($$insert into stockscout_unified_api.unified_alerts(user_id,name,ticker,mode,price_basis,payload) values('22222222-2222-2222-2222-222222222222','bad','BBB','next','split_div','{}'::jsonb)$$,'42501',null,'non-allowlisted user cannot create alert');
select is_empty($$update stockscout_unified_api.unified_drawings set ticker='BBB' returning 1$$,'non-owner cannot update owner drawing');
select is_empty($$delete from stockscout_unified_api.unified_alerts returning 1$$,'non-owner cannot delete owner alert');
select is((select count(*)::integer from stockscout_unified_api.unified_alert_state),0,'non-owner sees no runtime state');

reset role;
select ok(not has_function_privilege('anon','stockscout_unified_api.unified_set_watchlist_ticker(text,text,text,text,boolean)','execute'),'anon cannot call owner RPC');
select ok(has_function_privilege('authenticated','stockscout_unified_api.unified_set_watchlist_ticker(text,text,text,text,boolean)','execute'),'authenticated owner RPC remains RLS-bound');

select * from finish();
rollback;
