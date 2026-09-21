
import { NextResponse } from "next/server";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";
import { verifyPassword } from "../../../../lib/passwords";

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

  return NextResponse.json({
    id: u.id,
    username: u.username,
    company_name: u.company_name,
    logo_data_url: u.logo_data_url || ""
  });
}
