CREATE TABLE `user_events` (
	`event_id` text PRIMARY KEY NOT NULL,
	`event_name` text NOT NULL,
	`event_time` integer NOT NULL,
	`received_at` integer NOT NULL,
	`schema_version` text DEFAULT '1' NOT NULL,
	`environment` text NOT NULL,
	`channel` text DEFAULT 'direct' NOT NULL,
	`anonymous_user_id` text NOT NULL,
	`session_id` text NOT NULL,
	`page` text NOT NULL,
	`intelligence_id` text,
	`topic_id` text,
	`category` text,
	`position` integer,
	`properties_json` text DEFAULT '{}' NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_user_events_name_time` ON `user_events` (`event_name`,`event_time`);--> statement-breakpoint
CREATE INDEX `idx_user_events_session_time` ON `user_events` (`session_id`,`event_time`);--> statement-breakpoint
CREATE INDEX `idx_user_events_intelligence_name` ON `user_events` (`intelligence_id`,`event_name`);