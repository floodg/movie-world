-- Cloud account sync: private profile, favourites (with tombstones), and watch progress.
-- Clients use the publishable key only. Display name lives in profiles, never in JWT claims.

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "profiles_select_own"
  on public.profiles for select to authenticated
  using ((select auth.uid()) = id);

create policy "profiles_update_own"
  on public.profiles for update to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- Profile rows are created by the security-definer trigger below, not by clients.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    nullif(trim(coalesce(new.raw_user_meta_data ->> 'display_name', '')), '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create table public.favourites (
  user_id uuid not null references auth.users (id) on delete cascade,
  movie_id bigint not null,
  removed_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (user_id, movie_id)
);

create index favourites_user_updated_idx on public.favourites (user_id, updated_at desc);

alter table public.favourites enable row level security;

create policy "favourites_select_own"
  on public.favourites for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "favourites_insert_own"
  on public.favourites for insert to authenticated
  with check ((select auth.uid()) = user_id);

create policy "favourites_update_own"
  on public.favourites for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create table public.watch_progress (
  user_id uuid not null references auth.users (id) on delete cascade,
  movie_id bigint not null,
  position_seconds integer not null default 0 check (position_seconds >= 0),
  duration_seconds integer check (duration_seconds is null or duration_seconds >= 0),
  finished boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (user_id, movie_id)
);

create index watch_progress_user_updated_idx on public.watch_progress (user_id, updated_at desc);

alter table public.watch_progress enable row level security;

create policy "watch_progress_select_own"
  on public.watch_progress for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "watch_progress_insert_own"
  on public.watch_progress for insert to authenticated
  with check ((select auth.uid()) = user_id);

create policy "watch_progress_update_own"
  on public.watch_progress for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

revoke all on table public.profiles from anon;
revoke all on table public.favourites from anon;
revoke all on table public.watch_progress from anon;

grant select, update on table public.profiles to authenticated;
grant select, insert, update on table public.favourites to authenticated;
grant select, insert, update on table public.watch_progress to authenticated;

-- The signup trigger runs as the function owner; clients must not call it via RPC.
revoke execute on function public.handle_new_user() from public, anon, authenticated;
grant execute on function public.handle_new_user() to postgres, service_role;
