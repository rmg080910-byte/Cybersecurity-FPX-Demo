
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { defaultBanks, defaultSettings, defaultBankLogos } from "../../../lib/defaults";
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
      const bankName = defaultBanks[i];
      const presetLogo = defaultBankLogos[bankName] || null;

      await query(
        `INSERT INTO banks (name, enabled, sort_order, logo_data_url)
         VALUES ($1, TRUE, $2, $3)
         ON CONFLICT (name) DO UPDATE
         SET logo_data_url = CASE
           WHEN banks.logo_data_url IS NULL OR banks.logo_data_url = ''
           THEN EXCLUDED.logo_data_url
           ELSE banks.logo_data_url
         END`,
        [bankName, i, presetLogo]
      );
    }

    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ ok: false, error: e.message }, { status: 500 });
  }
}
