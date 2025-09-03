-- File: scripts/sql/005_add_contributions.sql
-- Description: Add contributions table to track user activity for contribution graph

-- Create contributions table
CREATE TABLE IF NOT EXISTS contributions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    contribution_date DATE NOT NULL,
    contribution_count INTEGER DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure one record per user per day
    UNIQUE(user_id, contribution_date)
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_contributions_user_id ON contributions(user_id);
CREATE INDEX IF NOT EXISTS idx_contributions_date ON contributions(contribution_date);
CREATE INDEX IF NOT EXISTS idx_contributions_user_date ON contributions(user_id, contribution_date);

-- Add RLS (Row Level Security)
ALTER TABLE contributions ENABLE ROW LEVEL SECURITY;

-- Create RLS policies
CREATE POLICY "Users can view their own contributions" ON contributions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own contributions" ON contributions
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own contributions" ON contributions
    FOR UPDATE USING (auth.uid() = user_id);

-- Add comments
COMMENT ON TABLE contributions IS 'Tracks daily user contributions for activity graph';
COMMENT ON COLUMN contributions.user_id IS 'ID of the user who made the contribution';
COMMENT ON COLUMN contributions.contribution_date IS 'Date of the contribution (YYYY-MM-DD)';
COMMENT ON COLUMN contributions.contribution_count IS 'Number of contributions made on this date';
