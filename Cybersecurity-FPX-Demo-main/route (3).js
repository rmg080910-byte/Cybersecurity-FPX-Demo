
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { defaultBanks, defaultSettings } from "../../../lib/defaults";
import { query } from "../../../lib/db";

export async function POST() {
  try {
    await ensureSchema();

    const row = await query(`SELECT data FROM settings WHERE id=1`);
    const current = row.rows[0]?.data || {};
    if (!Object.keys(current).length) {
      await query(`UPDATE settings SET data=$1::jsonb, updated_at=NOW() WHERE id=1`, [JSON.stringify(defaultSettings)]);
    }

    for (let i = 0; i < defaultBanks.length; i++) {
      await query(
        `INSERT INTO banks (name, enabled, sort_order)
         VALUES ($1, TRUE, $2)
         ON CONFLICT (name) DO NOTHING`,
        [defaultBanks[i], i]
      );
    }

    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ ok: false, error: e.message }, { status: 500 });
  }
}
