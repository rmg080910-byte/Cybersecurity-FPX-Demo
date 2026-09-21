STAFF PROFILE BANK/ADDRESS PATCH ONLY

Changed files:
- lib/schema.js
- app/api/salespersons/route.js
- app/api/salespersons/login/route.js
- app/admin/page.js
- app/page.js

Per staff editable fields:
- Salesperson Name
- BioMatrix ID
- Bank Name
- Bank Account
- Address
- Logo Size / Preview

Frontend top-right shows in English:
- Staff name
- BioMatrix ID
- Bank
- Bank Account
- Address

This page.js also consolidates recent fixes: Login label, IC 000000-00-0000 limit, username case status rule, random internal verification code.

After deploy, Logout then Login again to load the new profile fields.
