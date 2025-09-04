-- Add last_accessed and last_updated timestamps to folders table
ALTER TABLE public.folders 
ADD COLUMN IF NOT EXISTS last_accessed timestamptz DEFAULT now(),
ADD COLUMN IF NOT EXISTS last_updated timestamptz DEFAULT now();

-- Add last_updated timestamp to questions table
ALTER TABLE public.questions 
ADD COLUMN IF NOT EXISTS last_updated timestamptz DEFAULT now();

-- Update existing records to have proper timestamps
UPDATE public.folders 
SET last_accessed = created_at, last_updated = created_at 
WHERE last_accessed IS NULL OR last_updated IS NULL;

UPDATE public.questions 
SET last_updated = created_at 
WHERE last_updated IS NULL;

-- Create indexes for better performance on ordering
CREATE INDEX IF NOT EXISTS idx_folders_last_accessed ON public.folders(user_id, last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_folders_last_updated ON public.folders(user_id, last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_questions_last_updated ON public.questions(folder_id, last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_questions_user_last_updated ON public.questions(user_id, last_updated DESC);
