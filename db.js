
import pg from "pg";
const { Pool } = pg;

const globalForDb = globalThis;

export const pool =
  globalForDb.__demoPool ||
  new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: process.env.DATABASE_URL?.includes("railway") ? { rejectUnauthorized: false } : undefined,
  });

if (process.env.NODE_ENV !== "production") globalForDb.__demoPool = pool;

export async function query(text, params = []) {
  const result = await pool.query(text, params);
  return result;
}
