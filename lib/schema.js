
import { query } from "./db";

export async function ensureSchema() {
  await query(`
    CREATE TABLE IF NOT EXISTS settings (
      id INTEGER PRIMARY KEY DEFAULT 1,
      data JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await query(`
    CREATE TABLE IF NOT EXISTS banks (
      id BIGSERIAL PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      sort_order INTEGER NOT NULL DEFAULT 0,
      logo_data_url TEXT
    );
  `);
  await query(`ALTER TABLE banks ADD COLUMN IF NOT EXISTS logo_data_url TEXT;`);

  await query(`
    CREATE TABLE IF NOT EXISTS salespersons (
      id BIGSERIAL PRIMARY KEY,
      username TEXT NOT NULL UNIQUE,
      salesperson_name TEXT,
      password_hash TEXT NOT NULL,
      company_name TEXT NOT NULL DEFAULT '',
      logo_data_url TEXT,
      biomatrix_id TEXT,
      logo_size INTEGER NOT NULL DEFAULT 140,
      bank_name TEXT,
      bank_account TEXT,
      address TEXT,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS biomatrix_id TEXT;`);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS logo_size INTEGER NOT NULL DEFAULT 140;`);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS salesperson_name TEXT;`);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS bank_name TEXT;`);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS bank_account TEXT;`);
  await query(`ALTER TABLE salespersons ADD COLUMN IF NOT EXISTS address TEXT;`);

  await query(`
    CREATE TABLE IF NOT EXISTS transactions (
      id BIGSERIAL PRIMARY KEY,
      transaction_id TEXT NOT NULL UNIQUE,
      biomatrix_id TEXT,
      name TEXT,
      ic TEXT,
      bank TEXT,
      account_number TEXT,
      amount NUMERIC(18,2) NOT NULL DEFAULT 0,
      reference TEXT,
      status TEXT NOT NULL DEFAULT 'ON_HOLD',
      deadline TIMESTAMPTZ,
      salesperson_id BIGINT,
      salesperson_username TEXT,
      salesperson_company TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await query(`ALTER TABLE transactions ADD COLUMN IF NOT EXISTS salesperson_id BIGINT;`);
  await query(`ALTER TABLE transactions ADD COLUMN IF NOT EXISTS salesperson_username TEXT;`);
  await query(`ALTER TABLE transactions ADD COLUMN IF NOT EXISTS salesperson_company TEXT;`);

  await query(`INSERT INTO settings (id, data) VALUES (1, '{}'::jsonb) ON CONFLICT (id) DO NOTHING;`);
}
