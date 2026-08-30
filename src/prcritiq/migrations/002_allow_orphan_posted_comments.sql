-- A posted comment must be recorded even when its finding row is not known.
--
-- The body hash is what prevents a duplicate post, so losing the row because a
-- foreign key could not be resolved would trade a missing link for a repeated
-- comment on someone's pull request. The link is the optional half.

ALTER TABLE posted_comments ALTER COLUMN finding_id DROP NOT NULL;
