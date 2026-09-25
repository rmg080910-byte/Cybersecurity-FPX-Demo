
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";
import { defaultSettings } from "../../../lib/defaults";

export async function GET() {
  await ensureSchema();
  const r = await query(`SELECT data FROM settings WHERE id=1`);
  return NextResponse.json({ ...defaultSettings, ...(r.rows[0]?.data || {}) });
}

export async function PUT(req) {
  await ensureSchema();
  const body = await req.json();
  await query(`UPDATE settings SET data=$1::jsonb, updated_at=NOW() WHERE id=1`, [JSON.stringify(body)]);
  return NextResponse.json({ ok: true });
}
