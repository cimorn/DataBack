// Paths are ledger text, never filesystem input. Keep every slash and prefix intact.
export function displayPath(value: string): string {
  return value.replaceAll('\\', '/');
}

export function pathForSave(value: string, original?: string): string {
  // Showing a legacy path in the editor must not rewrite it when another field changes.
  if (original !== undefined && value === displayPath(original)) return original;
  return displayPath(value.trim());
}
