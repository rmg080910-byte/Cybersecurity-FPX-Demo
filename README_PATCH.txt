FIRST ROUND BANK LOGO PATCH

Upload only these changed files to the same paths in GitHub:
- lib/defaults.js
- app/api/init/route.js

What it does:
- Adds preset logos for common Malaysian banks.
- Existing manually-uploaded logos are NOT overwritten.
- You can later replace any logo from Admin > Banks > Upload Logo.

After GitHub commit and Railway deploy:
1. Open the frontend once (this triggers /api/init).
2. Refresh Admin > Banks.
3. The first-round banks should already show logos.
