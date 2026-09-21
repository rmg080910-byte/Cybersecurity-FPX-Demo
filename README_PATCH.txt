STAFF SAVE RELIABLE PATCH

Changed files:
- app/admin/page.js
- app/api/salespersons/route.js

Fixes:
- Save button now shows Saving...
- Success shows a confirmation alert.
- Server/API errors are shown instead of silently failing.
- PUT route validates User ID and Company Name and returns clear errors.
- Bank Name, Bank Account, Address, BioMatrix ID, Salesperson Name and Logo Size are all saved in the same request.
