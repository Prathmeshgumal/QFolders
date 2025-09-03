-- File: scripts/sql/004_add_star_ratings.sql
-- Description: Add star rating columns to questions table for auto-save functionality

-- Add star rating columns
ALTER TABLE questions
ADD COLUMN star1 BOOLEAN DEFAULT FALSE,
ADD COLUMN star2 BOOLEAN DEFAULT FALSE,
ADD COLUMN star3 BOOLEAN DEFAULT FALSE;

-- Add completion status column if it doesn't exist
ALTER TABLE questions
ADD COLUMN IF NOT EXISTS is_completed BOOLEAN DEFAULT FALSE;

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_questions_is_completed ON questions(is_completed);
CREATE INDEX IF NOT EXISTS idx_questions_star1 ON questions(star1);
CREATE INDEX IF NOT EXISTS idx_questions_star2 ON questions(star2);
CREATE INDEX IF NOT EXISTS idx_questions_star3 ON questions(star3);

-- Add comments
COMMENT ON COLUMN questions.star1 IS 'First star rating for the question';
COMMENT ON COLUMN questions.star2 IS 'Second star rating for the question';
COMMENT ON COLUMN questions.star3 IS 'Third star rating for the question';
COMMENT ON COLUMN questions.is_completed IS 'Whether the question is marked as completed';
