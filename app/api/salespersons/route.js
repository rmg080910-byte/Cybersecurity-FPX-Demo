
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";
import { hashPassword } from "../../../lib/passwords";

export async function GET() {
  await ensureSchema();
  const r = await query(`
    SELECT id, username, company_name, logo_data_url, enabled, created_at, updated_at
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
    const r = await query(`
      INSERT INTO salespersons (username, password_hash, company_name, logo_data_url, enabled)
      VALUES ($1,$2,$3,$4,$5)
      RETURNING id, username, company_name, logo_data_url, enabled, created_at, updated_at
    `, [username, hashPassword(password), company, b.logo_data_url || null, b.enabled !== false]);
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
  const r = await query(`
    UPDATE salespersons
    SET username=$1, password_hash=$2, company_name=$3, logo_data_url=$4, enabled=$5, updated_at=NOW()
    WHERE id=$6
    RETURNING id, username, company_name, logo_data_url, enabled, created_at, updated_at
  `, [
    String(b.username || current.rows[0].username).trim(),
    passwordHash,
    String(b.company_name || current.rows[0].company_name).trim(),
    b.logo_data_url || null,
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
