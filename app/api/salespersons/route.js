
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";
import { hashPassword } from "../../../lib/passwords";
import { generateBiomatrixId } from "../../../lib/biomatrix";

export async function GET() {
  await ensureSchema();
  const r = await query(`
    SELECT id, username, salesperson_name, company_name, logo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
    FROM salespersons
    ORDER BY id DESC
  `);
  return NextResponse.json(r.rows);
}

export async function POST(req) {
  await ensureSchema();
  const b = await req.json();
  const username = String(b.username || "").trim();
  const password = String(b.password || "");
  const company = String(b.company_name || "").trim();

  if (!username || !password || !company) {
    return NextResponse.json({ error: "Username, password and company name are required." }, { status: 400 });
  }

  try {
    const biomatrixId = String(b.biomatrix_id || "").trim() || generateBiomatrixId();
    const logoSize = Math.max(60, Math.min(260, Number(b.logo_size || 140)));
    const r = await query(`
      INSERT INTO salespersons (username, salesperson_name, password_hash, company_name, logo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
      RETURNING id, username, salesperson_name, company_name, logo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
    `, [username, String(b.salesperson_name || "").trim() || username, hashPassword(password), company, b.logo_data_url || null, biomatrixId, logoSize, String(b.bank_name || "").trim(), String(b.bank_account || "").trim(), String(b.address || "").trim(), b.enabled !== false]);
    return NextResponse.json(r.rows[0]);
  } catch (e) {
    if (String(e.message).toLowerCase().includes("unique")) {
      return NextResponse.json({ error: "Username already exists." }, { status: 409 });
    }
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req) {
  await ensureSchema();
  const b = await req.json();
  const id = Number(b.id);
  if (!id) return NextResponse.json({ error: "Invalid ID." }, { status: 400 });

  const current = await query(`SELECT * FROM salespersons WHERE id=$1`, [id]);
  if (!current.rows[0]) return NextResponse.json({ error: "Not found." }, { status: 404 });

  const passwordHash = b.password ? hashPassword(String(b.password)) : current.rows[0].password_hash;
  const biomatrixId = String(b.biomatrix_id || current.rows[0].biomatrix_id || "").trim() || generateBiomatrixId();
  const logoSize = Math.max(60, Math.min(260, Number(b.logo_size || current.rows[0].logo_size || 140)));
  const r = await query(`
    UPDATE salespersons
    SET username=$1, salesperson_name=$2, password_hash=$3, company_name=$4, logo_data_url=$5, biomatrix_id=$6, logo_size=$7, bank_name=$8, bank_account=$9, address=$10, enabled=$11, updated_at=NOW()
    WHERE id=$12
    RETURNING id, username, salesperson_name, company_name, logo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
  `, [
    String(b.username || current.rows[0].username).trim(),
    String(b.salesperson_name || current.rows[0].salesperson_name || b.username || current.rows[0].username).trim(),
    passwordHash,
    String(b.company_name || current.rows[0].company_name).trim(),
    b.logo_data_url || null,
    biomatrixId,
    logoSize,
    String(b.bank_name ?? current.rows[0].bank_name ?? "").trim(),
    String(b.bank_account ?? current.rows[0].bank_account ?? "").trim(),
    String(b.address ?? current.rows[0].address ?? "").trim(),
    b.enabled !== false,
    id
  ]);
  return NextResponse.json(r.rows[0]);
}

export async function DELETE(req) {
  await ensureSchema();
  const { searchParams } = new URL(req.url);
  const id = Number(searchParams.get("id"));
  if (!id) return NextResponse.json({ error: "Invalid ID." }, { status: 400 });
  await query(`DELETE FROM salespersons WHERE id=$1`, [id]);
  return NextResponse.json({ ok: true });
}
