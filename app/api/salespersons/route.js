
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";
import { hashPassword } from "../../../lib/passwords";
import { generateBiomatrixId } from "../../../lib/biomatrix";

export async function GET() {
  await ensureSchema();
  const r = await query(`
    SELECT id, username, salesperson_name, company_name, logo_data_url, photo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
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
      INSERT INTO salespersons (username, salesperson_name, password_hash, company_name, logo_data_url, photo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
      RETURNING id, username, salesperson_name, company_name, logo_data_url, photo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
    `, [username, String(b.salesperson_name || "").trim() || username, hashPassword(password), company, b.logo_data_url || null, b.photo_data_url || null, biomatrixId, logoSize, String(b.bank_name || "").trim(), String(b.bank_account || "").trim(), String(b.address || "").trim(), b.enabled !== false]);
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
  try {
    const b = await req.json();
    const id = Number(b.id);
    if (!id) return NextResponse.json({ error: "Invalid ID." }, { status: 400 });

    const current = await query(`SELECT * FROM salespersons WHERE id=$1`, [id]);
    if (!current.rows[0]) return NextResponse.json({ error: "Not found." }, { status: 404 });

    const c = current.rows[0];
    const username = String(b.username ?? c.username ?? "").trim();
    const companyName = String(b.company_name ?? c.company_name ?? "").trim();
    if (!username) return NextResponse.json({ error: "User ID is required." }, { status: 400 });
    if (!companyName) return NextResponse.json({ error: "Company Name is required." }, { status: 400 });

    const passwordHash = b.password ? hashPassword(String(b.password)) : c.password_hash;
    const biomatrixId = String(b.biomatrix_id ?? c.biomatrix_id ?? "").trim() || generateBiomatrixId();
    const logoSize = Math.max(60, Math.min(260, Number(b.logo_size ?? c.logo_size ?? 140)));

    const r = await query(`
      UPDATE salespersons
      SET username=$1,
          salesperson_name=$2,
          password_hash=$3,
          company_name=$4,
          logo_data_url=$5,
          photo_data_url=$6,
          biomatrix_id=$7,
          logo_size=$8,
          bank_name=$9,
          bank_account=$10,
          address=$11,
          enabled=$12,
          updated_at=NOW()
      WHERE id=$13
      RETURNING id, username, salesperson_name, company_name, logo_data_url, photo_data_url, biomatrix_id, logo_size, bank_name, bank_account, address, enabled, created_at, updated_at
    `, [
      username,
      String(b.salesperson_name ?? c.salesperson_name ?? username).trim() || username,
      passwordHash,
      companyName,
      b.logo_data_url ?? c.logo_data_url ?? null,
      b.photo_data_url ?? c.photo_data_url ?? null,
      biomatrixId,
      logoSize,
      String(b.bank_name ?? c.bank_name ?? "").trim(),
      String(b.bank_account ?? c.bank_account ?? "").trim(),
      String(b.address ?? c.address ?? "").trim(),
      b.enabled !== false,
      id
    ]);
    return NextResponse.json(r.rows[0]);
  } catch (e) {
    const msg = String(e?.message || e || "Unknown error");
    if (msg.toLowerCase().includes("unique") || msg.toLowerCase().includes("duplicate")) {
      return NextResponse.json({ error: "User ID already exists." }, { status: 409 });
    }
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

export async function DELETE(req) {
  await ensureSchema();
  const { searchParams } = new URL(req.url);
  const id = Number(searchParams.get("id"));
  if (!id) return NextResponse.json({ error: "Invalid ID." }, { status: 400 });
  await query(`DELETE FROM salespersons WHERE id=$1`, [id]);
  return NextResponse.json({ ok: true });
}
