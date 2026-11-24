import type { DocumentOut } from '@/api/types'

/** Enhanced (OCR) parsing is several times slower; measured on a 91-page annual report. */
export const ENHANCED_SLOWER_HINT = '增强模式更慢：91 页约 6 分钟（普通模式约 1 分钟）'

/**
 * Why a queued or parsing PDF/DOCX may take a while: the backend parses one of them at a time
 * (contracts §11), so a queue of large files can wait several minutes. TXT is parsed in-process.
 */
export function pendingHint(doc: DocumentOut): string | null {
  if (doc.file_type === 'txt') return null
  if (doc.status === 'queued') return '同一时间只解析一份 PDF/DOCX，排队可能需要几分钟'
  if (doc.status === 'parsing' && doc.parse_mode === 'enhanced') return ENHANCED_SLOWER_HINT
  return null
}
