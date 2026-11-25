import { useEffect, useState } from 'react'
import { ChevronDownIcon, ChevronRightIcon, RefreshCwIcon } from 'lucide-react'
import type { ChatScope, KnowledgeBaseOut } from '@/api/types'
import { listDocuments } from '@/api/documents'
import { listFolders } from '@/api/folders'
import { useResource } from '@/hooks/use-resource'
import { buildFolderTree, folderPath, subtreeIds, type FolderNode } from '@/lib/tree'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ErrorAlert } from '@/components/error-feedback'
import { AUTO_SCOPE, scopeIds, useChatScope } from './scope-context'

type ScopeField = 'knowledge_base_ids' | 'folder_ids' | 'document_ids'
export function ChatSources({ knowledgeBases }: { knowledgeBases: KnowledgeBaseOut[] }) {
  const [query, setQuery] = useState('')
  return <div className="flex min-h-0 flex-1 flex-col gap-4">
    <div><h2 className="text-sm font-semibold">资料范围</h2><p className="mt-1 text-xs text-muted-foreground">选择这次交流使用的知识</p></div>
    <div className="max-h-60 shrink-0 overflow-y-auto"><ScopeSummary /></div>
    <Input aria-label="搜索资料" placeholder="搜索知识库、目录或文档…" value={query} onChange={(event) => setQuery(event.target.value)} />
    <p className="text-xs leading-relaxed text-muted-foreground">展开浏览，勾选限定范围。不选择时 AI 按需查找你的知识库。</p>
    <div className="min-h-0 flex-1 overflow-y-auto">
      {knowledgeBases.map((kb) => <SourceLibrary key={kb.id} kb={kb} query={query.trim().toLocaleLowerCase()} />)}
      {!knowledgeBases.length && <p className="text-sm text-muted-foreground">还没有知识库，也可以直接与 AI 交流。在文件页创建知识库并上传资料。</p>}
    </div>
  </div>
}

function SourceLibrary({ kb, query }: { kb: KnowledgeBaseOut; query: string }) {
  const [open, setOpen] = useState(false)
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => new Set())
  const { scope, names, setScope } = useChatScope()
  const folders = useResource(open || query ? 'scope-folders:' + kb.id : null, () => listFolders(kb.id))
  const documents = useResource(open || query ? 'scope-docs:' + kb.id : null, () => listDocuments(kb.id))
  const pending = (documents.data ?? []).some((doc) => doc.status === 'queued' || doc.status === 'parsing')
  const { reload: reloadFolders } = folders
  const { reload: reloadDocuments } = documents
  useEffect(() => {
    if (!open && !query) return
    const refresh = () => { void reloadFolders({ silent: true }); void reloadDocuments({ silent: true }) }
    const timer = setInterval(refresh, pending ? 2000 : 15000)
    window.addEventListener('focus', refresh)
    return () => { clearInterval(timer); window.removeEventListener('focus', refresh) }
  }, [open, query, pending, reloadFolders, reloadDocuments])
  const toggle = (field: ScopeField, id: string, label: string, checked: boolean) => {
    const next: ChatScope = { ...scope, mode: 'selected', [field]: checked ? [...scope[field], id] : scope[field].filter((value) => value !== id) }
    setScope(scopeIds(next).length ? next : AUTO_SCOPE, { ...names, [id]: label })
  }
  const fullLibrary = scope.knowledge_base_ids.includes(kb.id)
  const folderList = folders.data ?? []
  const selectedFolders = scope.folder_ids.flatMap((id) => [...subtreeIds(folderList, id)])
  const row = (field: ScopeField, id: string, label: string, inherited = false, disabled = false) => <label className={"flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-2 text-sm " + (inherited || scope[field].includes(id) ? "bg-accent text-accent-foreground ring-1 ring-border" : "hover:bg-muted/60")}>
    <Checkbox aria-label={'选择' + label} checked={inherited || scope[field].includes(id)} disabled={inherited || disabled} onCheckedChange={(checked) => toggle(field, id, label, checked === true)} />
    <span className="min-w-0 break-all line-clamp-2" title={label}>{label}</span>
  </label>
  const documentRows = (folderId: string | null) =>
    (documents.data ?? []).filter((doc) => doc.folder_id === folderId).map((doc) => <div className="pl-3" key={doc.id}>
      {row('document_ids', doc.id, doc.filename, fullLibrary || Boolean(doc.folder_id && selectedFolders.includes(doc.folder_id)), doc.status !== 'success')}
      {doc.status !== 'success' && <p className="pb-2 text-xs text-muted-foreground">{doc.status === 'failed' ? '解析失败，请在文件页重试' : '尚未解析完成，暂不可选'}</p>}
    </div>)
  const folderRow = (node: FolderNode) => {
    const folderId = node.folder.id
    const expanded = expandedFolders.has(folderId)
    const inherited = fullLibrary || folderList.some((folder) => folder.id !== folderId && scope.folder_ids.includes(folder.id) && subtreeIds(folderList, folder.id).has(folderId))
    return <div key={folderId}>
      <div className="flex items-center gap-1">
        <Button size="icon-xs" variant="ghost" aria-label={(expanded ? '收起目录' : '展开目录') + node.folder.name} aria-expanded={expanded} onClick={() => setExpandedFolders((current) => {
          const next = new Set(current)
          if (expanded) next.delete(folderId)
          else next.add(folderId)
          return next
        })}>{expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}</Button>
        {row('folder_ids', folderId, folderPath(folderList, folderId).join(' / ') + ' · 含子目录', inherited)}
      </div>
      {expanded && <div className="ml-3 border-l pl-2">{documentRows(folderId)}{node.children.map(folderRow)}</div>}
    </div>
  }
  return <section className="border-b py-1">
    <div className="flex items-center gap-1">
      <Button size="icon-xs" variant="ghost" aria-label={(open ? '收起' : '展开') + kb.name} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <ChevronDownIcon /> : <ChevronRightIcon />}</Button>
      {row('knowledge_base_ids', kb.id, kb.name + ' · 全库')}
    </div>
    {(open || query) && <div className="ml-3 border-l pl-3">
      <Button size="xs" variant="ghost" onClick={() => { void folders.reload(); void documents.reload() }}><RefreshCwIcon />刷新资料</Button>
      {folders.error && <ErrorAlert error={folders.error} onRetry={() => void folders.reload()} forceRetry />}
      {documents.error && <ErrorAlert error={documents.error} onRetry={() => void documents.reload()} forceRetry />}
      {(folders.loading || documents.loading) && <p className="text-xs text-muted-foreground">正在加载资料…</p>}
      {query ? (() => {
        const matchesLibrary = kb.name.toLocaleLowerCase().includes(query)
        const matchingFolders = folderList.filter((folder) => matchesLibrary || folderPath(folderList, folder.id).join('/').toLocaleLowerCase().includes(query))
        const matchingDocuments = (documents.data ?? []).filter((doc) => matchesLibrary || (doc.filename + '/' + folderPath(folderList, doc.folder_id).join('/')).toLocaleLowerCase().includes(query))
        return <>
          {matchingFolders.map((folder) => <div key={folder.id}>{row('folder_ids', folder.id, folderPath(folderList, folder.id).join(' / ') + ' · 含子目录', fullLibrary)}</div>)}
          {matchingDocuments.map((doc) => <div key={doc.id}>
            {row('document_ids', doc.id, doc.filename, fullLibrary || Boolean(doc.folder_id && selectedFolders.includes(doc.folder_id)), doc.status !== 'success')}
            <p className="pl-6 text-xs text-muted-foreground">{folderPath(folderList, doc.folder_id).join(' / ') || '根目录'}{doc.status !== 'success' ? ' · 尚未解析成功' : ''}</p>
          </div>)}
          {!documents.loading && !folders.loading && !matchingDocuments.length && !matchingFolders.length && <p className="py-2 text-xs text-muted-foreground">此知识库没有匹配的资料</p>}
        </>
      })() : <>{documentRows(null)}{buildFolderTree(folderList).map(folderRow)}</>}

      {!query && documents.data?.length === 0 && !documents.loading && <p className="py-2 text-xs text-muted-foreground">此知识库暂无文件</p>}
    </div>}
  </section>
}

export function ScopeSummary() {
  const { scope, names, issues, verifyScope, setScope } = useChatScope()
  if (scope.mode === 'auto') return <div className="rounded-lg border border-primary/10 bg-accent/50 px-3 py-2 text-sm"><span className="font-medium">自动查找资料</span><p className="text-xs text-muted-foreground">AI 按需检索你的知识库</p></div>
  return <div className="flex max-h-44 flex-wrap items-center gap-1.5 overflow-y-auto text-xs">
    <span className="w-full font-semibold text-primary">仅使用所选资料</span>
    {(['knowledge_base_ids', 'folder_ids', 'document_ids'] as const).flatMap((field) => scope[field].map((id) => <Button key={id} size="xs" variant={issues[id] ? "destructive" : "outline"} title={issues[id]} className="h-auto max-w-full whitespace-normal break-all text-left" aria-label={'移除' + (names[id] ?? id)} onClick={() => {
      const next = { ...scope, [field]: scope[field].filter((value) => value !== id) }
      setScope(scopeIds(next).length ? next : AUTO_SCOPE)
    }}>{names[id] ?? '未命名资料'}{issues[id] ? '（不可用）' : ''} ×</Button>))}
    {Object.keys(issues).length > 0 && <div className="w-full rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive" role="alert">
      {Object.entries(issues).map(([id, message]) => <p key={id}>{names[id] ?? '所选资料'}：{message}</p>)}
      <p>请移除失效选择或等待恢复，当前范围不会自动扩大。</p>
      <Button size="xs" variant="outline" onClick={() => void verifyScope()}>重新检查资料</Button>
    </div>}
    <Button size="xs" variant="ghost" onClick={() => setScope(AUTO_SCOPE)}>恢复自动</Button>
  </div>
}
