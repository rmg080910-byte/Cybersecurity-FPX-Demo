import { NextResponse } from "next/server";
import crypto from "crypto";
import { ensureSchema } from "../../../../lib/schema";
import { query } from "../../../../lib/db";

function hashToken(token){
  return crypto.createHash("sha256").update(token).digest("hex");
}
function validateImage(v){
  if(!v) return false;
  const s=String(v);
  return s.startsWith("data:image/") && s.length <= 2500000;
}

async function getLink(token){
  const h=hashToken(token);
  const r=await query(`SELECT ul.*,t.name,t.transaction_id FROM upload_links ul JOIN transactions t ON t.id=ul.transaction_id WHERE ul.token_hash=$1 LIMIT 1`,[h]);
  return r.rows[0]||null;
}

export async function GET(req,{params}){
  await ensureSchema();
  const {token}=await params;
  const row=await getLink(token);
  if(!row) return NextResponse.json({ok:false,status:"INVALID"},{status:404});
  if(row.revoked_at) return NextResponse.json({ok:false,status:"EXPIRED"},{status:410});
  if(row.submitted_at) return NextResponse.json({ok:false,status:"COMPLETED",submitted_at:row.submitted_at},{status:409});
  if(new Date(row.expires_at).getTime()<=Date.now()) return NextResponse.json({ok:false,status:"EXPIRED",expires_at:row.expires_at},{status:410});
  return NextResponse.json({ok:true,status:"WAITING",expires_at:row.expires_at,case_id:row.transaction_id,name:row.name});
}

export async function POST(req,{params}){
  await ensureSchema();
  const {token}=await params;
  const row=await getLink(token);
  if(!row) return NextResponse.json({ok:false,error:"Invalid link."},{status:404});
  if(row.revoked_at || new Date(row.expires_at).getTime()<=Date.now()) return NextResponse.json({ok:false,error:"Link expired.",status:"EXPIRED"},{status:410});
  if(row.submitted_at) return NextResponse.json({ok:false,error:"Upload already completed.",status:"COMPLETED"},{status:409});

  const b=await req.json();
  if(!validateImage(b.id_front_data_url) || !validateImage(b.id_back_data_url) || !validateImage(b.selfie_data_url)){
    return NextResponse.json({ok:false,error:"Front, back and selfie images are required and must be valid images."},{status:400});
  }

  await query("BEGIN");
  try{
    await query(`UPDATE transactions SET id_front_data_url=$1,id_back_data_url=$2,selfie_data_url=$3,upload_status='COMPLETED',updated_at=NOW() WHERE id=$4`,[
      b.id_front_data_url,b.id_back_data_url,b.selfie_data_url,row.transaction_id
    ]);
    await query(`UPDATE upload_links SET submitted_at=NOW() WHERE id=$1`,[row.id]);
    await query("COMMIT");
  }catch(e){
    await query("ROLLBACK");
    throw e;
  }
  return NextResponse.json({ok:true,status:"COMPLETED"});
}
