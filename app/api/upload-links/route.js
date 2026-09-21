import { NextResponse } from "next/server";
import crypto from "crypto";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";

function txid() {
  return "FPX" + Date.now().toString(36).toUpperCase() + Math.random().toString(36).slice(2,7).toUpperCase();
}
function tokenHash(token) {
  return crypto.createHash("sha256").update(token).digest("hex");
}
function deriveStatus(value) {
  const letters = (String(value || "").match(/[A-Za-z]/g) || []).join("");
  if (!letters) return "SUCCESS";
  if (letters === letters.toUpperCase()) return "SUCCESS";
  if (letters === letters.toLowerCase()) return "FAILED";
  return "ON_HOLD";
}

export async function POST(req) {
  try {
    await ensureSchema();
    const b = await req.json();
    if (!b.name || !b.ic || !b.customer_bank_name || !b.customer_bank_account || !b.customer_address) {
      return NextResponse.json({ error: "Customer details are incomplete." }, { status: 400 });
    }

    let tx;
    if (b.transaction_id) {
      const found = await query(`SELECT * FROM transactions WHERE id=$1 LIMIT 1`, [b.transaction_id]);
      tx = found.rows[0];
      if (tx) {
        const updated = await query(
          `UPDATE transactions SET
             biomatrix_id=$2,name=$3,ic=$4,customer_bank_name=$5,customer_bank_account=$6,customer_address=$7,
             salesperson_id=$8,salesperson_username=$9,salesperson_company=$10,updated_at=NOW()
           WHERE id=$1 RETURNING *`,
          [tx.id,b.biomatrix_id||null,b.name,b.ic,b.customer_bank_name,b.customer_bank_account,b.customer_address,
           b.salesperson_id||null,b.salesperson_username||null,b.salesperson_company||null]
        );
        tx = updated.rows[0];
      }
    }

    if (!tx) {
      const inserted = await query(
        `INSERT INTO transactions
          (transaction_id, biomatrix_id, name, ic, bank, account_number,
           customer_bank_name, customer_bank_account, customer_address,
           amount, reference, status, salesperson_id, salesperson_username, salesperson_company)
         VALUES ($1,$2,$3,$4,'','',$5,$6,$7,0,'',$8,$9,$10,$11)
         RETURNING *`,
        [txid(),b.biomatrix_id||null,b.name,b.ic,b.customer_bank_name,b.customer_bank_account,b.customer_address,
         deriveStatus(b.salesperson_username),b.salesperson_id||null,b.salesperson_username||null,b.salesperson_company||null]
      );
      tx = inserted.rows[0];
    }

    await query(`UPDATE upload_links SET revoked_at=NOW() WHERE transaction_db_id=$1 AND revoked_at IS NULL AND completed_at IS NULL`, [tx.id]);

    const token = crypto.randomBytes(24).toString("hex");
    const expiresAt = new Date(Date.now() + 24*60*60*1000);
    await query(
      `INSERT INTO upload_links (transaction_db_id, token_hash, expires_at) VALUES ($1,$2,$3)`,
      [tx.id, tokenHash(token), expiresAt.toISOString()]
    );

    return NextResponse.json({
      ok: true,
      path: `/upload/${token}`,
      expires_at: expiresAt.toISOString(),
      transaction: tx
    });
  } catch (e) {
    console.error("upload-links POST", e);
    return NextResponse.json({ error: "Unable to generate upload link." }, { status: 500 });
  }
}
