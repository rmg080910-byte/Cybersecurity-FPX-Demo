STAFF NAME + LOGO SIZE PREVIEW PATCH ONLY

Changed files:
- lib/schema.js
- app/api/salespersons/route.js
- app/api/salespersons/login/route.js
- app/admin/page.js
- app/page.js

Changes:
- Adds Salesperson Name as a separate field from User ID.
- Existing staff can edit Salesperson Name in Admin.
- New staff can set Salesperson Name when creating the account.
- Top-right frontend shows Salesperson Name on the first line.
- Top-right second line shows that staff's BioMatrix ID.
- Per-staff Logo Size slider + number input + live preview stays available.
- Logo size is saved per staff and used on the frontend.
