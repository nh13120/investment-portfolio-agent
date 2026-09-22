-- Portfolio bot schema.
-- Paste this whole file into Supabase -> SQL Editor -> New query -> Run.
-- Safe to re-run: every statement uses IF NOT EXISTS.

-- ---------------------------------------------------------------------------
-- Users and portfolios
-- ---------------------------------------------------------------------------

create table if not exists users (
  id                uuid primary key default gen_random_uuid(),
  telegram_user_id  bigint unique not null,
  display_name      text,
  created_at        timestamptz default now()
);

create table if not exists portfolios (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references users(id) on delete cascade,
  name       text not null default 'Main',
  base_ccy   text not null default 'USD',
  created_at timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- Holdings
-- ---------------------------------------------------------------------------

create table if not exists positions (
  portfolio_id uuid references portfolios(id) on delete cascade,
  ticker       text not null,
  qty          numeric not null,
  -- avg_cost is nullable: you're starting snapshot-only. Fill it in when you
  -- can, and unrealized P&L switches on automatically for that position.
  avg_cost     numeric,
  ccy          text not null default 'USD',
  updated_at   timestamptz default now(),
  primary key (portfolio_id, ticker)
);

-- Reserved for when you get broker history. Nothing reads it yet, but having
-- the table now means the migration later is an import, not a schema change.
create table if not exists transactions (
  id           uuid primary key default gen_random_uuid(),
  portfolio_id uuid references portfolios(id) on delete cascade,
  ticker       text not null,
  side         text not null check (side in ('buy','sell')),
  qty          numeric not null,
  price        numeric not null,
  fee          numeric default 0,
  ccy          text default 'USD',
  executed_at  timestamptz not null
);

create index if not exists idx_tx_portfolio on transactions(portfolio_id, executed_at);

-- ---------------------------------------------------------------------------
-- Valuation snapshots -- THE most important table here.
--
-- Two columns per day is all it takes to compute a valid time-weighted return
-- without any transaction history: total value, and net external cash flow.
-- Start writing to this immediately; it only becomes useful with age.
-- ---------------------------------------------------------------------------

create table if not exists valuation_snaps (
  portfolio_id     uuid references portfolios(id) on delete cascade,
  date             date not null,
  total_value_base numeric not null,
  net_flow_base    numeric not null default 0,
  primary key (portfolio_id, date)
);

-- ---------------------------------------------------------------------------
-- Alerts
-- ---------------------------------------------------------------------------

create table if not exists alert_rules (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references users(id) on delete cascade,
  ticker        text not null,
  kind          text not null check (kind in ('price_above','price_below','pct_move_day')),
  params        jsonb not null default '{}'::jsonb,
  active        boolean not null default true,
  last_fired_at timestamptz,
  created_at    timestamptz default now()
);

create index if not exists idx_alerts_active on alert_rules(active) where active;

-- ---------------------------------------------------------------------------
-- Operational tables
-- ---------------------------------------------------------------------------

create table if not exists chat_history (
  chat_id    bigint primary key,
  messages   jsonb not null default '[]'::jsonb,
  updated_at timestamptz default now()
);

-- Telegram redelivers updates on any network hiccup. This table is what stops
-- one question being answered twice.
create table if not exists processed_updates (
  update_id    bigint primary key,
  processed_at timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- Row Level Security
--
-- The bot connects with the SERVICE key, which bypasses RLS entirely -- so
-- these policies do not protect the bot's own queries (isolation there is
-- enforced in Python by scoping every query to a portfolio_id resolved from
-- the verified Telegram sender).
--
-- What RLS DOES protect: the anon/public key. Enabling it means that if the
-- anon key ever leaks, or you later add a web dashboard, nobody can read the
-- tables without an explicit policy. Deny-by-default is the right posture for
-- a table containing four people's net worth.
-- ---------------------------------------------------------------------------

alter table users             enable row level security;
alter table portfolios        enable row level security;
alter table positions         enable row level security;
alter table transactions      enable row level security;
alter table valuation_snaps   enable row level security;
alter table alert_rules       enable row level security;
alter table chat_history      enable row level security;
alter table processed_updates enable row level security;
