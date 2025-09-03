-- Add terminal_output column to questions table
alter table public.questions 
add column if not exists terminal_output text null;
