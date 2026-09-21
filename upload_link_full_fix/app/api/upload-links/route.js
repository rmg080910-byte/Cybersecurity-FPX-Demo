import { NextResponse } from "next/server";
import crypto from "crypto";
import { ensureSchema } from "../../../lib/schema";
import { query } from "../../../lib/db";

function txid() {
  return "FPX" + Date.now().toString(36).toUpperCase() + Math.random().toString(36).slice(2,7).toUpperCase();
}
function tokenHash(token){
  return crypto.createHash("sha256").update(token).digest("hex");
}

export async function POST(req){
  await ensureSchema();
  const b=await req.json();
  if(!String(b.name||"").trim() || !String(b.ic||"").trim()){
    return NextResponse.json({error:"Name and IC are required."},{status:400});
  }

  let transaction;
  if(b.transaction_id){
    const r=await query(`UPDATE transactions SET biomatrix_id=$1,name=$2,ic=$3,customer_bank_name=$4,customer_bank_account=$5,customer_address=$6,salesperson_id=$7,salesperson_username=$8,salesperson_company=$9,updated_at=NOW() WHERE id=$10 RETURNING *`,[
      b.biomatrix_id||null,b.name,b.ic,b.customer_bank_name||"",b.customer_bank_account||"",b.customer_address||"",b.salesperson_id||null,b.salesperson_username||null,b.salesperson_company||null,b.transaction_id
    ]);
    transaction=r.rows[0];
  }else{
    const r=await query(`INSERT INTO transactions (transaction_id,biomatrix_id,name,ic,customer_bank_name,customer_bank_account,customer_address,status,salesperson_id,salesperson_username,salesperson_company) VALUES ($1,$2,$3,$4,$5,$6,$7,'ON_HOLD',$8,$9,$10) RETURNING *`,[
      txid(),b.biomatrix_id||null,b.name,b.ic,b.customer_bank_name||"",b.customer_bank_account||"",b.customer_address||"",b.salesperson_id||null,b.salesperson_username||null,b.salesperson_company||null
    ]);
    transaction=r.rows[0];
  }
  if(!transaction) return NextResponse.json({error:"Unable to create case."},{status:500});

  await query(`UPDATE upload_links SET revoked_at=NOW() WHERE transaction_id=$1 AND submitted_at IS NULL AND revoked_at IS NULL`,[transaction.id]);
  const token=crypto.randomBytes(32).toString("hex");
  const hash=tokenHash(token);
  await query(`INSERT INTO upload_links (transaction_id,token_hash,expires_at) VALUES ($1,$2,NOW()+INTERVAL '24 hours')`,[transaction.id,hash]);

  return NextResponse.json({transaction,path:`/upload/${token}`,expires_in_hours:24});
}
