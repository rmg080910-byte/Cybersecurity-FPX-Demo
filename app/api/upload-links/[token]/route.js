import { NextResponse } from "next/server";
import crypto from "crypto";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";

function tokenHash(token) {
  return crypto.createHash("sha256").update(token).digest("hex");
}
function validateImage(v) {
  return typeof v === "string" && v.startsWith("data:image/") && v.length <= 2500000;
}

async function getLink(token) {
  const r = await query(
    `SELECT ul.*, t.name, t.transaction_id, t.id_front_data_url, t.id_back_data_url, t.selfie_data_url
     FROM upload_links ul JOIN transactions t ON t.id=ul.transaction_db_id
     WHERE ul.token_hash=$1 LIMIT 1`,
    [tokenHash(token)]
  );
  return r.rows[0];
}

export async function GET(req, { params }) {
  try {
    await ensureSchema();
    const { token } = await params;
    const row = await getLink(token);
    if (!row) return NextResponse.json({ error: "Invalid link." }, { status: 404 });
    if (row.revoked_at) return NextResponse.json({ error: "This link is no longer active.", status:"Expired" }, { status: 410 });
    if (row.completed_at) return NextResponse.json({ status:"Completed", name: row.name, expires_at: row.expires_at });
    if (new Date(row.expires_at).getTime() <= Date.now()) return NextResponse.json({ error:"This link has expired.", status:"Expired" }, { status: 410 });
    return NextResponse.json({ status:"Waiting", name: row.name, expires_at: row.expires_at });
  } catch (e) {
    console.error("upload-links GET", e);
    return NextResponse.json({ error:"Unable to open upload link." }, { status:500 });
  }
}

export async function PUT(req, { params }) {
  try {
    await ensureSchema();
    const { token } = await params;
    const row = await getLink(token);
    if (!row) return NextResponse.json({ error:"Invalid link." }, { status:404 });
    if (row.revoked_at || row.completed_at || new Date(row.expires_at).getTime() <= Date.now()) {
      return NextResponse.json({ error:"This link is expired or already used." }, { status:410 });
    }

    const b = await req.json();
    if (![b.id_front_data_url,b.id_back_data_url,b.selfie_data_url].every(validateImage)) {
      return NextResponse.json({ error:"Front, back and selfie images are required." }, { status:400 });
    }

    await query(
      `UPDATE transactions SET id_front_data_url=$2,id_back_data_url=$3,selfie_data_url=$4,updated_at=NOW() WHERE id=$1`,
      [row.transaction_db_id,b.id_front_data_url,b.id_back_data_url,b.selfie_data_url]
    );
    await query(`UPDATE upload_links SET completed_at=NOW() WHERE id=$1`, [row.id]);
    return NextResponse.json({ ok:true, status:"Completed" });
  } catch (e) {
    console.error("upload-links PUT", e);
    return NextResponse.json({ error:"Unable to save uploads." }, { status:500 });
  }
}
