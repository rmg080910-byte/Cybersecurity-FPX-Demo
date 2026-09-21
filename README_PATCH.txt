STAFF BIOMATRIX PATCH ONLY

Changed files:
- lib/schema.js
- lib/biomatrix.js
- app/api/salespersons/login/route.js
- app/api/salespersons/route.js
- app/page.js
- app/admin/page.js

What this patch does:
- Every staff account gets its own BioMatrix ID.
- If you leave BioMatrix blank when creating staff, the system auto-generates one.
- The frontend shows the logged-in staff's BioMatrix ID instead of the global one.
- Old staff accounts without a BioMatrix ID get one automatically at login.

After upload:
1. Upload these files to GitHub in the same paths.
2. Commit.
3. Wait for Railway deploy.
4. Login as different staff to see different BioMatrix IDs.
