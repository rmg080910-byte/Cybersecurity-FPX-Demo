
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";

export async function GET(req, { params }) {
  await ensureSchema();
  const { id } = await params;
  const r = await query(`SELECT * FROM transactions WHERE id=$1 LIMIT 1`, [id]);
  return NextResponse.json(r.rows[0] || null);
}

export async function PUT(req, { params }) {
  await ensureSchema();
  const { id } = await params;
  const b = await req.json();
  const hasDeadline = Object.prototype.hasOwnProperty.call(b,"deadline");
  const hasUploadRemark = Object.prototype.hasOwnProperty.call(b,"upload_custom_status");

  const r = await query(
    `UPDATE transactions
     SET status=COALESCE($1,status),
         deadline=CASE WHEN $2 THEN $3 ELSE deadline END,
         upload_custom_status=CASE WHEN $4 THEN $5 ELSE upload_custom_status END,
         updated_at=NOW()
     WHERE id=$6
     RETURNING *`,
    [
      b.status || null,
      hasDeadline,
      b.deadline ?? null,
      hasUploadRemark,
      b.upload_custom_status ?? "",
      id
    ]
  );
  return NextResponse.json(r.rows[0] || null);
}
