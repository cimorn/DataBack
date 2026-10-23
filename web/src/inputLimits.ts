export const MAX_NOTES_CHARS = 2000;
export const MAX_RECORDS_PER_BATCH = 20;
export const MAX_PASSWORD_CHARS = 1024;

export function notesForSave(value: string, previous?: string): string {
  if (previous !== undefined && value === previous) return previous;
  const notes = value.trim();
  if (notes.length > MAX_NOTES_CHARS) throw new Error(`备注最多 ${MAX_NOTES_CHARS} 字。`);
  return notes;
}
