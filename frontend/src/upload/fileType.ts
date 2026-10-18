// Browsers often report HEIC (and sometimes others) with an empty type, so fall back to the
// extension. The server re-checks the real bytes when it processes the photo.

const BY_EXTENSION: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  heic: 'image/heic',
  heif: 'image/heif',
}

export function contentTypeFor(file: Pick<File, 'name' | 'type'>, allowed: string[]): string | null {
  if (allowed.includes(file.type)) return file.type
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  const guess = BY_EXTENSION[extension]
  return guess && allowed.includes(guess) ? guess : null
}
