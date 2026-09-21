# Cybersecurity FPX / e-KYC Project

Clean project structure for Railway + PostgreSQL.

## Routes
- `/` frontend
- `/admin` admin

## Railway
Required variable: `DATABASE_URL` referencing the Railway PostgreSQL service.

## Included features
- Staff login first page
- Per-staff name, company, logo, logo size, BioMatrix ID, bank details and address
- IC format limit `000000-00-0000`
- Identity front/back upload + camera selfie
- Saved KYC images attached to transactions
- Bank list with uploadable logos and modal logo preview
- Case-based status rule from staff User ID
- Internal 6-digit verification code
- Transaction status control in Admin

Use synthetic/test identity images for tutorial and testing. Do not enter real bank OTP/TAC credentials.
