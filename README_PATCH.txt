KYC IMAGE BACKEND SAVE PATCH

Changed files:
- app/page.js
- app/admin/page.js
- app/api/transactions/route.js
- lib/schema.js

What it does:
- ID Front, ID Back and Selfie are compressed in the browser.
- They are saved with the transaction in PostgreSQL.
- Admin > Transactions > Open shows all 3 images.
- Clicking an image opens the saved image.
- Existing transactions continue to work and simply show "Not uploaded" for old records.

Privacy:
Use synthetic/test identity images for tutorial/demo testing. Real identity documents and selfies are sensitive personal data and should only be collected with appropriate consent, access controls, retention/deletion rules and legal compliance.
