
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

  const imageFields = [b.id_front_data_url, b.id_back_data_url, b.selfie_data_url];
  for (const image of imageFields) {
    if (image && (!String(image).startsWith("data:image/") || String(image).length > 2500000)) {
      return NextResponse.json({ error: "Invalid or oversized image." }, { status: 413 });
    }
  }

  const r = await query(
    `INSERT INTO transactions
      (transaction_id, biomatrix_id, name, ic, bank, account_number,
       customer_bank_name, customer_bank_account, customer_address,
       amount, reference, status, deadline,
       salesperson_id, salesperson_username, salesperson_company,
       id_front_data_url, id_back_data_url, selfie_data_url)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
     RETURNING *`,
    [
      id,b.biomatrix_id,b.name,b.ic,b.bank,b.account_number,
      b.customer_bank_name||"",b.customer_bank_account||"",b.customer_address||"",
      b.amount||0,b.reference||"",
      b.status||"ON_HOLD",b.deadline||null,
      b.salesperson_id||null,b.salesperson_username||null,b.salesperson_company||null,
      b.id_front_data_url||null,b.id_back_data_url||null,b.selfie_data_url||null
    ]
  );
  return NextResponse.json(r.rows[0]);
}
