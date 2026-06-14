-- Shared cross-engine P&L ledger. Every fill/period across all engines (arbgrid,
-- quant-engine, ...) lands here tagged engine/lane/tax_bucket, so the daily digest
-- and the day-90 review roll up P&L in one place. Idempotent: safe to re-run.
-- (Sits after the rewards schema 0001/0002; the pnl table is independent of them.)

create table if not exists pnl (
  id          uuid primary key default gen_random_uuid(),
  engine      text not null,                  -- 'arbgrid', 'quant', ...
  lane        text not null,                  -- 'prediction-markets', 'perp_carry', 'sports', ...
  tax_bucket  text not null
              check (tax_bucket in ('ordinary', 'possible_1256', 'gambling')),
  amount_usd  numeric(14, 4) not null,        -- signed realized P&L
  trade_date  date not null,
  asset       text,
  notes       text,
  created_at  timestamptz not null default now()
);

create index if not exists pnl_engine_date_idx on pnl (engine, trade_date);
create index if not exists pnl_lane_date_idx on pnl (lane, trade_date);
create index if not exists pnl_tax_bucket_idx on pnl (tax_bucket, trade_date);

-- Deny-by-default RLS: written only by server-side service-role jobs (which bypass
-- RLS); this locks the anon key out of P&L. Add policies only if a client read path
-- is ever needed.
alter table public.pnl enable row level security;
