
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";
import { verifyPassword } from "../../../../lib/passwords";
import { generateBiomatrixId } from "../../../../lib/biomatrix";

export async function POST(req) {
  await ensureSchema();
  const b = await req.json();
  const username = String(b.username || "").trim();
  const password = String(b.password || "");

  const r = await query(`SELECT * FROM salespersons WHERE username=$1 AND enabled=TRUE LIMIT 1`, [username]);
  const u = r.rows[0];

  if (!u || !verifyPassword(password, u.password_hash)) {
    return NextResponse.json({ error: "Invalid username or password." }, { status: 401 });
  }

  let biomatrix_id = u.biomatrix_id;
  if (!biomatrix_id) {
    biomatrix_id = generateBiomatrixId();
    await query(`UPDATE salespersons SET biomatrix_id=$1, updated_at=NOW() WHERE id=$2`, [biomatrix_id, u.id]);
  }

  return NextResponse.json({
    id: u.id,
    username: u.username,
    salesperson_name: u.salesperson_name || u.username,
    company_name: u.company_name,
    logo_data_url: u.logo_data_url || "",
    logo_size: Number(u.logo_size || 140),
    bank_name: u.bank_name || "",
    bank_account: u.bank_account || "",
    address: u.address || "",
    biomatrix_id
  });
}
