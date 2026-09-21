SECURITY HARDENING PATCH

This patch DOES NOT hide platform logs or make a service untraceable.

Adds:
- Security headers
- Clickjacking protection
- Referrer restrictions
- Permissions restrictions
- CSP
- Server-side admin login cookie
- HttpOnly + Secure + SameSite=Strict admin session
- 8-hour admin session expiry

Railway Variables to add:
1. ADMIN_PASSWORD
2. ADMIN_SESSION_SECRET

Important:
- Use a long random ADMIN_SESSION_SECRET (at least 32 random characters).
- Keep DATABASE_URL private and do not expose it to the browser.
- Railway/GitHub infrastructure logs still exist normally.

Files:
- middleware.js
- lib/adminAuth.js
- app/api/admin/login/route.js
- app/api/admin/logout/route.js
- app/api/admin/session/route.js
- .env.security.example

This is a security foundation patch. Your existing /admin page can be connected to these three API routes for server-side login.
