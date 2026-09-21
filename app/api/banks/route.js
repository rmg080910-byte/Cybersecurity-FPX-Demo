
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";

export async function GET() {
  await ensureSchema();
  const r = await query(`SELECT id,name,enabled,sort_order FROM banks ORDER BY sort_order,name`);
  return NextResponse.json(r.rows);
}

export async function PUT(req) {
  await ensureSchema();
  const banks = await req.json();
  for (const [i, b] of banks.entries()) {
    await query(`UPDATE banks SET enabled=$1, sort_order=$2 WHERE id=$3`, [!!b.enabled, i, b.id]);
  }
  return NextResponse.json({ ok: true });
}
