export function generateBiomatrixId() {
  const n = Math.floor(1000000000 + Math.random() * 9000000000);
  return `BioMatrix CR-${n}`;
}
