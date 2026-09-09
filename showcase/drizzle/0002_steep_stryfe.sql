CREATE TABLE `quality_reviews` (
	`review_id` text PRIMARY KEY NOT NULL,
	`case_key` text NOT NULL,
	`source_type` text NOT NULL,
	`intelligence_id` text,
	`keyword` text,
	`bad_case_type` text NOT NULL,
	`occurrence_count` integer DEFAULT 1 NOT NULL,
	`status` text DEFAULT 'open' NOT NULL,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	`resolved_at` integer
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_quality_reviews_case_key` ON `quality_reviews` (`case_key`);--> statement-breakpoint
CREATE INDEX `idx_quality_reviews_status_seen` ON `quality_reviews` (`status`,`last_seen_at`);--> statement-breakpoint
CREATE INDEX `idx_quality_reviews_source_seen` ON `quality_reviews` (`source_type`,`last_seen_at`);