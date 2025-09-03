-- Add PDF file columns to questions table
-- This script adds support for storing PDF file references in the questions table

-- Add columns for PDF file storage
ALTER TABLE questions 
ADD COLUMN pdf_file_name TEXT,
ADD COLUMN pdf_file_path TEXT,
ADD COLUMN pdf_file_size BIGINT,
ADD COLUMN pdf_file_uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Add index for better query performance
CREATE INDEX IF NOT EXISTS idx_questions_pdf_file_path ON questions(pdf_file_path);

-- Add comment to document the new columns
COMMENT ON COLUMN questions.pdf_file_name IS 'Original name of the uploaded PDF file';
COMMENT ON COLUMN questions.pdf_file_path IS 'Path to the PDF file in Supabase storage bucket';
COMMENT ON COLUMN questions.pdf_file_size IS 'Size of the PDF file in bytes';
COMMENT ON COLUMN questions.pdf_file_uploaded_at IS 'Timestamp when the PDF file was uploaded';
