
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";

export async function GET() {
  await ensureSchema();
  const r = await query(`SELECT id,name,enabled,sort_order,logo_data_url FROM banks ORDER BY sort_order,name`);
  return NextResponse.json(r.rows);
}

export async function PUT(req) {
  await ensureSchema();
  const banks = await req.json();
  for (const [i, b] of banks.entries()) {
    await query(`UPDATE banks SET enabled=$1, sort_order=$2, logo_data_url=$3 WHERE id=$4`, [!!b.enabled, i, b.logo_data_url || null, b.id]);
  }
  return NextResponse.json({ ok: true });
}
