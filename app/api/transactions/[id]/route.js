
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
  const hasReviewStatus = Object.prototype.hasOwnProperty.call(b,"upload_review_status");
  const hasRemark = Object.prototype.hasOwnProperty.call(b,"upload_remark");

  const r = await query(
    `UPDATE transactions
     SET status=COALESCE($1,status),
         deadline=CASE WHEN $2 THEN $3 ELSE deadline END,
         upload_review_status=CASE WHEN $4 THEN $5 ELSE upload_review_status END,
         upload_remark=CASE WHEN $6 THEN $7 ELSE upload_remark END,
         updated_at=NOW()
     WHERE id=$8
     RETURNING *`,
    [
      b.status || null,
      hasDeadline,
      b.deadline ?? null,
      hasReviewStatus,
      b.upload_review_status ?? "",
      hasRemark,
      b.upload_remark ?? "",
      id
    ]
  );
  return NextResponse.json(r.rows[0] || null);
}
