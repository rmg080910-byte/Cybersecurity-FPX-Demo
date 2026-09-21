LOGIN FIRST PAGE PATCH ONLY

Changed file:
- app/page.js

New flow:
1. Login page
2. e-KYC / Open File page
3. Identity Verification
4. Select Bank
5. Confirmation
6. Verification
7. Result / Receipt

Behavior:
- The e-KYC page is not shown until staff login succeeds.
- Logout returns directly to the Login page.
- Existing saved staff session still skips Login until Logout.
