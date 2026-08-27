begin;
select plan(18);

insert into auth.users (id,email) values
  ('11111111-1111-1111-1111-111111111111','owner@example.test'),
  ('22222222-2222-2222-2222-222222222222','other@example.test');
insert into stockscout_unified_api.owner_allowlist(user_id)
values('11111111-1111-1111-1111-111111111111');

select ok(not has_schema_privilege('anon','stockscout_unified_api','usage'),'anon cannot use the owner schema');
select ok(not has_table_privilege('anon','stockscout_unified_api.unified_drawings','select'),'anon cannot read drawings');
select ok(not has_table_privilege('anon','stockscout_unified_api.unified_alerts','insert'),'anon cannot create alerts');
select ok(has_schema_privilege('authenticated','stockscout_unified_api','usage'),'authenticated role can use the owner schema');
select ok(has_table_privilege('authenticated','stockscout_unified_api.unified_drawings','select,insert,update,delete'),'authenticated role has drawing operations gated by RLS');
select ok(has_table_privilege('authenticated','stockscout_unified_api.unified_alerts','select,insert,update,delete'),'authenticated role has alert operations gated by RLS');

set local role authenticated;
select set_config('request.jwt.claims','{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}',true);
select lives_ok($$insert into stockscout_unified_api.unified_drawings(user_id,ticker,interval,mode,price_basis,payload) values('11111111-1111-1111-1111-111111111111','AAA','daily','next','split_div','{"version":1,"type":"horizontal","points":[]}'::jsonb)$$,'allowlisted owner creates own drawing');
select lives_ok($$insert into stockscout_unified_api.unified_alerts(user_id,name,ticker,mode,price_basis,payload) values('11111111-1111-1111-1111-111111111111','AAA above 10','AAA','next','split_div','{"version":1,"kind":"price","operator":"above","price":10}'::jsonb)$$,'allowlisted owner creates own alert');
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

reset role;
select ok(not has_function_privilege('anon','stockscout_unified_api.unified_set_watchlist_ticker(text,text,text,text,boolean)','execute'),'anon cannot call owner RPC');
select ok(has_function_privilege('authenticated','stockscout_unified_api.unified_set_watchlist_ticker(text,text,text,text,boolean)','execute'),'authenticated owner RPC remains RLS-bound');

select * from finish();
rollback;
