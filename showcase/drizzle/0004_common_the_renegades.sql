CREATE TABLE `site_snapshots` (
	`snapshot_date` text PRIMARY KEY NOT NULL,
	`payload_json` text NOT NULL,
	`record_count` integer DEFAULT 0 NOT NULL,
	`source` text DEFAULT 'feishu' NOT NULL,
	`generated_at` integer NOT NULL,
	`refreshed_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_site_snapshots_refreshed` ON `site_snapshots` (`refreshed_at`);