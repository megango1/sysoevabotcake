-- Run this in Supabase SQL Editor to create required tables

create table if not exists users (
    user_id      bigint primary key,
    username     text,
    full_name    text,
    has_access   boolean not null default false,
    access_until timestamptz,
    created_at   timestamptz not null default now(),
    notified_7d  boolean not null default false,
    notified_3d  boolean not null default false,
    notified_1d  boolean not null default false
);

create table if not exists sections (
    id            serial primary key,
    parent_key    text not null,
    title         text not null,
    emoji         text,
    content       text,
    photo_file_id text,
    video_file_id text,
    is_active     boolean not null default true,
    created_at    timestamptz not null default now()
);

create index if not exists sections_parent_key_idx on sections (parent_key);

create table if not exists payments (
    id          serial primary key,
    user_id     bigint not null,
    full_name   text,
    username    text,
    amount_uah  numeric(10,2) not null,
    currency    text not null default 'UAH',
    email       text,
    days        int not null,
    paid_at     timestamptz not null default now()
);

create index if not exists payments_paid_at_idx on payments (paid_at desc);

-- ── Payment requests (manual screenshot approval) ─────────────────────────────
create table if not exists payment_requests (
    id                  serial primary key,
    user_id             bigint not null unique,
    username            text,
    full_name           text,
    screenshot_file_id  text,
    status              text not null default 'pending',  -- pending / approved / rejected
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists payment_requests_status_idx on payment_requests (status);
create index if not exists payment_requests_created_idx on payment_requests (created_at desc);

-- ─── Run this if the users table already exists ───────────────────────────────
-- alter table users add column if not exists notified_7d boolean not null default false;
-- alter table users add column if not exists notified_3d boolean not null default false;
-- alter table users add column if not exists notified_1d boolean not null default false;
