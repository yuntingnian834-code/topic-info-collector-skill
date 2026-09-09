CREATE TABLE `intelligence_feedback` (
	`feedback_id` text PRIMARY KEY NOT NULL,
	`intelligence_id` text NOT NULL,
	`anonymous_user_id` text NOT NULL,
	`session_id` text NOT NULL,
	`feedback_value` text NOT NULL,
	`reason` text,
	`channel` text DEFAULT 'direct' NOT NULL,
	`page_path` text DEFAULT '/intelligence-detail.html' NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_intelligence_feedback_user_article` ON `intelligence_feedback` (`anonymous_user_id`,`intelligence_id`);--> statement-breakpoint
CREATE INDEX `idx_intelligence_feedback_article` ON `intelligence_feedback` (`intelligence_id`);--> statement-breakpoint
CREATE INDEX `idx_intelligence_feedback_updated` ON `intelligence_feedback` (`updated_at`);