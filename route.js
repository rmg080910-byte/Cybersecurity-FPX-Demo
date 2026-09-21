
import { NextResponse } from "next/server";
import crypto from "crypto";
import { makeAdminSession, adminCookieName, adminSessionMaxAge } from "../../../../../lib/adminAuth";

function sameText(a, b) {
  const aa = Buffer.from(String(a || ""));
  const bb = Buffer.from(String(b || ""));
  if (aa.length !== bb.length) return false;
  return crypto.timingSafeEqual(aa, bb);
}

export async function POST(req) {
  const body = await req.json();
  const expected = process.env.ADMIN_PASSWORD;

  if (!expected) {
    return NextResponse.json(
      { ok: false, error: "ADMIN_PASSWORD is not configured." },
      { status: 500 }
    );
  }

  if (!sameText(body.password, expected)) {
    return NextResponse.json({ ok: false, error: "Invalid password." }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(adminCookieName, makeAdminSession(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: adminSessionMaxAge,
    path: "/",
  });
  return res;
}
