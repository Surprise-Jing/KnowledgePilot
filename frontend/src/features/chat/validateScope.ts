import { toApiError } from '@/api/client'
import { getDocument } from '@/api/documents'
import { listFolders } from '@/api/folders'
import { listKnowledgeBases } from '@/api/knowledgeBases'
import type { ChatScope } from '@/api/types'

export async function validateScope(scope: ChatScope): Promise<Record<string, string>> {
  const issues: Record<string, string> = {}
  if (scope.mode === 'auto') return issues
  await Promise.all(scope.document_ids.map(async (id) => {
    try {
      const document = await getDocument(id)
      if (document.status !== 'success') {
        issues[id] = document.status === 'failed' ? '解析失败，请重试解析或移除选择' : '正在重新解析，暂不可用于提问'
      }
    } catch (error) {
      issues[id] = toApiError(error).status === 404 ? '文档已删除或不再可访问' : '暂时无法确认文档状态，请刷新重试'
    }
  }))
  if (scope.knowledge_base_ids.length || scope.folder_ids.length) {
    try {
      const libraries = await listKnowledgeBases()
      for (const id of scope.knowledge_base_ids) {
        if (!libraries.some((library) => library.id === id)) issues[id] = '知识库已删除或不再可访问'
      }
      if (scope.folder_ids.length) {
        const folders = (await Promise.all(libraries.map((kb) => listFolders(kb.id)))).flat()
        for (const id of scope.folder_ids) {
          if (!folders.some((folder) => folder.id === id)) issues[id] = '目录已删除或不再可访问'
        }
      }
    } catch {
      for (const id of [...scope.knowledge_base_ids, ...scope.folder_ids]) {
        issues[id] = '暂时无法确认资料范围，请刷新重试'
      }
    }
  }
  return issues
}
