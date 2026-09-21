UPLOAD LINK FULL FIX

Fixes the current error:
Unexpected token '<', '<!DOCTYPE ... is not valid JSON

Cause: the frontend was calling /api/upload-links but the deployed server returned an HTML page instead of the JSON API response (typically because the upload-link API route was missing/not deployed).

This patch includes the required pieces together:
- app/page.js
- app/api/upload-links/route.js
- app/api/upload-links/[token]/route.js
- app/upload/[token]/page.js
- lib/schema.js
- public/ekyc-guide.png

Flow:
1. Fill customer details.
2. Continue.
3. Processing runs automatically for about 2.5 seconds.
4. The customer upload link appears automatically.
5. Copy and send the link.
6. Customer uploads ID front, ID back and selfie.

Upload links expire after 24 hours; regenerated links revoke the prior active link.
