
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";

function txid() {
  return "FPX" + Date.now().toString(36).toUpperCase() + Math.random().toString(36).slice(2,7).toUpperCase();
}

export async function GET() {
  await ensureSchema();
  const r = await query(`SELECT * FROM transactions ORDER BY created_at DESC LIMIT 500`);
  return NextResponse.json(r.rows);
}

export async function POST(req) {
  await ensureSchema();
  const b = await req.json();
  const id = txid();
  const r = await query(
    `INSERT INTO transactions
      (transaction_id, biomatrix_id, name, ic, bank, account_number, amount, reference, status, deadline)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
     RETURNING *`,
    [id,b.biomatrix_id,b.name,b.ic,b.bank,b.account_number,b.amount||0,b.reference||"",b.status||"ON_HOLD",b.deadline||null]
  );
  return NextResponse.json(r.rows[0]);
}
