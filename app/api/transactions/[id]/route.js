
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";

export async function PUT(req, { params }) {
  await ensureSchema();
  const { id } = await params;
  const b = await req.json();
  const r = await query(
    `UPDATE transactions
     SET status=COALESCE($1,status),
         deadline=COALESCE($2,deadline),
         updated_at=NOW()
     WHERE id=$3
     RETURNING *`,
    [b.status || null, b.deadline || null, id]
  );
  return NextResponse.json(r.rows[0] || null);
}
