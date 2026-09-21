
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { verifyAdminSession, adminCookieName } from "../../../../../lib/adminAuth";

export async function GET() {
  const c = await cookies();
  const ok = verifyAdminSession(c.get(adminCookieName)?.value);
  return NextResponse.json({ ok });
}
