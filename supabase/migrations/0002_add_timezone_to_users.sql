-- Add timezone column to users table
-- This allows the agent to know the user's timezone for interpreting relative time queries

alter table public.users
    add column if not exists timezone text;

-- Note: We don't set a default here because the application code
-- requires users to explicitly set their timezone. Existing users
-- will need to set their timezone through the app settings.
