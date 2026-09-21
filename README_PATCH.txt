SELFIE UPLOAD ONLY PATCH

Changed file:
- app/page.js

Changes:
- Removed camera opening/capture flow.
- Selfie now works exactly like ID Front and ID Back:
  - Upload Selfie
  - Preview Selfie
  - Replace Selfie
- Continue stays disabled until Front + Back + Selfie are all present.
- Existing transaction save logic still sends the selfie image with the transaction.
