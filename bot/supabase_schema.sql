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

-- Index for fast parent_key lookups
create index if not exists sections_parent_key_idx on sections (parent_key);

-- ─── Run this if the users table already exists ───────────────────────────────
-- alter table users add column if not exists notified_7d boolean not null default false;
-- alter table users add column if not exists notified_3d boolean not null default false;
-- alter table users add column if not exists notified_1d boolean not null default false;
