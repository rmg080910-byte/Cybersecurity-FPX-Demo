STATUS + IC FORMAT PATCH ONLY

Changed file:
- app/page.js

Status rule uses the logged-in Staff User ID and ignores spaces, numbers and symbols:
- all letters UPPERCASE => SUCCESS
- all letters lowercase => FAILED
- mixed uppercase/lowercase => ON_HOLD

The status is decided before the transaction is created; it is no longer forced to ON_HOLD.

IC format:
- 12 digits maximum
- displayed as 000000-00-0000
- 14 characters total including the two hyphens
- spaces are removed
