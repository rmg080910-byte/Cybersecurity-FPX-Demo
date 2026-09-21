
# CYBERSECURITY FPX Demo — Frontend + All-in-One Admin

Two pages only:

- `/` — frontend demo
- `/admin` — all-in-one admin control panel

## What the admin can change

- Logo upload / replace / remove
- Background image upload / replace / remove
- Brand name, merchant name, header subtitle
- Logo size and position
- Background / primary / success / failed / on-hold colors
- Frontend labels and result messages
- BioMatrix label/value
- Failed and OnHold timer hours
- Bank enable/disable and ordering
- Transactions list and status override

The frontend reads settings from PostgreSQL, so changes made in `/admin` affect the frontend without editing source code.

## Demo status rule

The frontend uses the **Demo User ID** only for simulation:

- letters all uppercase → Successful
- letters all lowercase → Failed
- mixed uppercase/lowercase → OnHold
- numbers/symbols only → Successful

The demo password is not stored in PostgreSQL.

## Local setup

1. Install Node.js 20+
2. Create a PostgreSQL database.
3. Copy `.env.example` to `.env.local`
4. Set `DATABASE_URL`
5. Run:

```bash
npm install
npm run dev
```

6. Open:
   - frontend: `http://localhost:3000`
   - admin: `http://localhost:3000/admin`

Demo admin password: `admin123`

## Railway + GitHub

1. Create a new GitHub repo.
2. Upload this project.
3. In Railway create a new project from the GitHub repo.
4. Add a PostgreSQL service.
5. Add the PostgreSQL `DATABASE_URL` to the web service variables.
6. Railway build command: `npm run build`
7. Railway start command: `npm start`
8. Deploy.

The app initializes its tables automatically on first use.

## Important demo limitation

This project is intended as a simulation/training demo. It does **not** connect to FPX, any real bank, real OTP/TAC, or real KYC systems. Do not enter real banking credentials or real OTP/TAC.


## Bank logo upload

In `/admin` → `banks`, every bank now has:

- Upload Logo
- Remove Logo
- Enable / disable
- Reorder

Uploaded bank logos are stored with the bank record in PostgreSQL and shown on the frontend bank selection screen.


## Staff / salesperson multi-brand login

Admin now has a `salespersons` tab.

For each salesperson you can set:
- User ID
- Password
- Company name
- Company logo
- Enabled / disabled

Frontend behavior:
- Staff Login is in the top-right.
- Customer/payment page no longer contains staff User ID / Password fields.
- After staff login, the header automatically switches to that salesperson's company name and logo.
- Each transaction stores the salesperson username and company.
- Transaction result page polls the backend, so an Admin status change can update an open frontend transaction.
