
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";


function deriveCaseStatus(value) {
  const letters = (String(value || "").match(/[A-Za-z]/g) || []).join("");
  if (!letters) return "SUCCESS";
  if (letters === letters.toUpperCase()) return "SUCCESS";
  if (letters === letters.toLowerCase()) return "FAILED";
  return "ON_HOLD";
}

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
  const hasFront = Object.prototype.hasOwnProperty.call(b,"id_front_data_url");
  const hasBack = Object.prototype.hasOwnProperty.call(b,"id_back_data_url");
  const hasSelfie = Object.prototype.hasOwnProperty.call(b,"selfie_data_url");

  const imageFields = [
    hasFront ? b.id_front_data_url : null,
    hasBack ? b.id_back_data_url : null,
    hasSelfie ? b.selfie_data_url : null
  ];

  for (const image of imageFields) {
    if (image && (!String(image).startsWith("data:image/") || String(image).length > 2500000)) {
      return NextResponse.json({ error: "Invalid or oversized image." }, { status: 413 });
    }
  }

  const r = await query(
    `UPDATE transactions
     SET status=COALESCE($1,status),
         deadline=CASE WHEN $2 THEN $3 ELSE deadline END,
         upload_review_status=CASE WHEN $4 THEN $5 ELSE upload_review_status END,
         upload_remark=CASE WHEN $6 THEN $7 ELSE upload_remark END,

         biomatrix_id=CASE WHEN $8 THEN $9 ELSE biomatrix_id END,
         name=CASE WHEN $10 THEN $11 ELSE name END,
         ic=CASE WHEN $12 THEN $13 ELSE ic END,
         bank=CASE WHEN $14 THEN $15 ELSE bank END,
         account_number=CASE WHEN $16 THEN $17 ELSE account_number END,
         amount=CASE WHEN $18 THEN $19 ELSE amount END,
         reference=CASE WHEN $20 THEN $21 ELSE reference END,
         customer_bank_name=CASE WHEN $22 THEN $23 ELSE customer_bank_name END,
         customer_bank_account=CASE WHEN $24 THEN $25 ELSE customer_bank_account END,
         customer_address=CASE WHEN $26 THEN $27 ELSE customer_address END,

         id_front_data_url=CASE WHEN $28 THEN $29 ELSE id_front_data_url END,
         id_back_data_url=CASE WHEN $30 THEN $31 ELSE id_back_data_url END,
         selfie_data_url=CASE WHEN $32 THEN $33 ELSE selfie_data_url END,

         upload_status=CASE
           WHEN
             COALESCE(CASE WHEN $28 THEN $29 ELSE id_front_data_url END,'') <> ''
             AND COALESCE(CASE WHEN $30 THEN $31 ELSE id_back_data_url END,'') <> ''
             AND COALESCE(CASE WHEN $32 THEN $33 ELSE selfie_data_url END,'') <> ''
           THEN 'COMPLETED'
           ELSE COALESCE(upload_status,'WAITING')
         END,

         updated_at=NOW()
     WHERE id=$34
     RETURNING *`,
    [
      Object.prototype.hasOwnProperty.call(b,"name") ? deriveCaseStatus(b.name) : (b.status || null),
      hasDeadline,
      b.deadline ?? null,
      hasReviewStatus,
      b.upload_review_status ?? "",
      hasRemark,
      b.upload_remark ?? "",

      Object.prototype.hasOwnProperty.call(b,"biomatrix_id"), b.biomatrix_id ?? null,
      Object.prototype.hasOwnProperty.call(b,"name"), b.name ?? "",
      Object.prototype.hasOwnProperty.call(b,"ic"), b.ic ?? "",
      Object.prototype.hasOwnProperty.call(b,"bank"), b.bank ?? "",
      Object.prototype.hasOwnProperty.call(b,"account_number"), b.account_number ?? "",
      Object.prototype.hasOwnProperty.call(b,"amount"), Number(b.amount || 0),
      Object.prototype.hasOwnProperty.call(b,"reference"), b.reference ?? "",
      Object.prototype.hasOwnProperty.call(b,"customer_bank_name"), b.customer_bank_name ?? "",
      Object.prototype.hasOwnProperty.call(b,"customer_bank_account"), b.customer_bank_account ?? "",
      Object.prototype.hasOwnProperty.call(b,"customer_address"), b.customer_address ?? "",

      hasFront, b.id_front_data_url ?? null,
      hasBack, b.id_back_data_url ?? null,
      hasSelfie, b.selfie_data_url ?? null,

      id
    ]
  );

  return NextResponse.json(r.rows[0] || null);
}


export async function DELETE(req, { params }) {
  await ensureSchema();
  const { id } = await params;

  try {
    await query(`DELETE FROM upload_links WHERE transaction_id=$1`, [id]);
  } catch (_) {}

  const r = await query(`DELETE FROM transactions WHERE id=$1 RETURNING id,transaction_id`, [id]);

  if(!r.rows[0]) {
    return NextResponse.json({ok:false,error:"Case not found."},{status:404});
  }

  return NextResponse.json({ok:true,deleted:r.rows[0]});
}
