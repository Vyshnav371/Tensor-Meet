// Keep the browser's visual wake hint aligned with backend/app/wake.py.
// The server transcript remains the only source of commands and approvals.
const tensor = String.raw`(?:tensor|tenser|tensar|tensir|tensa|tense[\s-]?or|tenzor|tenzer|ten[\s-]?(?:sir|sor|zer|zor)|tencer)`;
const separators = String.raw`[\s,.!?:;"'“”‘’—–-]*`;
const greeting = new RegExp(
  String.raw`\b(?:(?:hey|hi|hello|hay|ok(?:ay)?|yo)${separators}(?:${tensor}|dancer)|a${separators}${tensor})\b${separators}`,
  'i',
);
const addressed = new RegExp(String.raw`^${separators}${tensor}\b${separators}`, 'i');

export function hasWakeWord(text: string): boolean {
  return greeting.test(text) || addressed.test(text);
}
