IDENTITY CARD + SELFIE STEP PATCH ONLY

Changed file:
- app/page.js

New flow:
1. Payment Details
2. Identity Verification
   - Upload Identification Card Front
   - Upload Identification Card Back
   - Open Camera
   - Take Selfie
3. Select Bank
5. Transaction Confirmation
6. Verification
7. Processing
8. Result
9. Receipt

Important:
- ID images and selfie are previewed only in the browser for this step.
- This patch does not upload or store ID/selfie images on the server or database.
- Camera access requires browser permission and HTTPS. Railway's public domain uses HTTPS.
