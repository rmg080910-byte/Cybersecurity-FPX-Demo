
import crypto from "crypto";

const COOKIE = "admin_session";
const MAX_AGE = 60 * 60 * 8;

function secret() {
  return process.env.ADMIN_SESSION_SECRET || "change-this-in-railway";
}

export function makeAdminSession() {
  const ts = Math.floor(Date.now() / 1000);
  const payload = `admin:${ts}`;
  const sig = crypto.createHmac("sha256", secret()).update(payload).digest("hex");
  return `${payload}:${sig}`;
}

export function verifyAdminSession(value) {
  if (!value) return false;
  const parts = String(value).split(":");
  if (parts.length !== 3) return false;

  const [role, tsRaw, sig] = parts;
  if (role !== "admin") return false;

  const ts = Number(tsRaw);
  if (!Number.isFinite(ts)) return false;
  if (Math.floor(Date.now() / 1000) - ts > MAX_AGE) return false;

  const payload = `${role}:${tsRaw}`;
  const expected = crypto.createHmac("sha256", secret()).update(payload).digest("hex");

  try {
    return crypto.timingSafeEqual(Buffer.from(sig, "hex"), Buffer.from(expected, "hex"));
  } catch {
    return false;
  }
}

export const adminCookieName = COOKIE;
export const adminSessionMaxAge = MAX_AGE;
