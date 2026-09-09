import { desc, eq, gt, lt } from "drizzle-orm";
import { getDb } from "./index";
import { siteSnapshots } from "./schema";


export type StoredSiteSnapshot = {
  snapshotDate: string;
  payload: Record<string, unknown>;
  recordCount: number;
  source: string;
  generatedAt: number;
  refreshedAt: number;
};


function decode(row: typeof siteSnapshots.$inferSelect): StoredSiteSnapshot | null {
  try {
    return {
      snapshotDate: row.snapshotDate,
      payload: JSON.parse(row.payloadJson) as Record<string, unknown>,
      recordCount: row.recordCount,
      source: row.source,
      generatedAt: row.generatedAt,
      refreshedAt: row.refreshedAt,
    };
  } catch {
    return null;
  }
}


export async function getSiteSnapshot(
  snapshotDate: string,
): Promise<StoredSiteSnapshot | null> {
  const [row] = await getDb()
    .select()
    .from(siteSnapshots)
    .where(eq(siteSnapshots.snapshotDate, snapshotDate))
    .limit(1);
  return row ? decode(row) : null;
}


export async function getLatestSuccessfulSiteSnapshot(): Promise<StoredSiteSnapshot | null> {
  const [row] = await getDb()
    .select()
    .from(siteSnapshots)
    .where(gt(siteSnapshots.recordCount, 0))
    .orderBy(desc(siteSnapshots.snapshotDate))
    .limit(1);
  return row ? decode(row) : null;
}


export async function saveSiteSnapshot(
  snapshotDate: string,
  payload: Record<string, unknown>,
  recordCount: number,
): Promise<void> {
  const now = Date.now();
  const generatedAt = Date.parse(
    String((payload.meta as Record<string, unknown> | undefined)?.generatedAt ?? ""),
  );
  const db = getDb();
  await db
    .insert(siteSnapshots)
    .values({
      snapshotDate,
      payloadJson: JSON.stringify(payload),
      recordCount,
      source: "feishu",
      generatedAt: Number.isFinite(generatedAt) ? generatedAt : now,
      refreshedAt: now,
    })
    .onConflictDoUpdate({
      target: siteSnapshots.snapshotDate,
      set: {
        payloadJson: JSON.stringify(payload),
        recordCount,
        source: "feishu",
        generatedAt: Number.isFinite(generatedAt) ? generatedAt : now,
        refreshedAt: now,
      },
    });

  const cutoff = now - 35 * 24 * 60 * 60 * 1000;
  await db.delete(siteSnapshots).where(lt(siteSnapshots.refreshedAt, cutoff));
}
