import { BrainCircuitIcon, FilesIcon, LibraryIcon, MenuIcon, MessagesSquareIcon, PanelRightIcon } from 'lucide-react'
import { type ReactNode, useState } from 'react'

import { listFolders } from '@/api/folders'
import { listKnowledgeBases } from '@/api/knowledgeBases'
import { ErrorProvider } from '@/components/error-feedback'
import { HealthBadge } from '@/components/health-badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Toaster } from '@/components/ui/sonner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TooltipProvider } from '@/components/ui/tooltip'
import { UserSwitcher } from '@/components/user-switcher'
import { ChatView } from '@/features/chat/ChatView'
import { DocumentViewer } from '@/features/citations/DocumentViewer'
import { InspectorPanel } from '@/features/citations/InspectorPanel'
import { InspectorProvider } from '@/features/citations/InspectorProvider'
import { useInspector } from '@/features/citations/inspector-context'
import { FilesView } from '@/features/files/FilesView'
import { KnowledgeSidebar } from '@/features/knowledge/KnowledgeSidebar'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useResource } from '@/hooks/use-resource'
import { getCurrentUser, setCurrentUser } from '@/lib/user'

export default function App() {
  const [userId, setUserId] = useState(getCurrentUser)
  const switchUser = (next: string) => {
    if (next === userId) return
    setCurrentUser(next)
    setUserId(next)
  }
  return (
    <TooltipProvider>
      <ErrorProvider>
        {(banner) => (
          // Keyed by user: switching users drops all state and reloads every list (isolation demo).
          <InspectorProvider key={userId}>
            <Workspace userId={userId} onSwitchUser={switchUser} banner={banner} />
          </InspectorProvider>
        )}
      </ErrorProvider>
      <Toaster position="top-center" />
    </TooltipProvider>
  )
}

type CenterView = 'files' | 'chat'

function Workspace({
  userId,
  onSwitchUser,
  banner,
}: {
  userId: string
  onSwitchUser: (userId: string) => void
  banner: ReactNode
}) {
  const isWide = useMediaQuery('(min-width: 1280px)')
  const isMedium = useMediaQuery('(min-width: 768px)')
  const { panelOpen, setPanelOpen } = useInspector()
  const [navOpen, setNavOpen] = useState(false)
  const [view, setView] = useState<CenterView>('files')

  const knowledgeBases = useResource('knowledge-bases', listKnowledgeBases)
  const kbList = knowledgeBases.data ?? []
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null)
  const kb = kbList.find((k) => k.id === selectedKbId) ?? kbList[0] ?? null
  const kbId = kb?.id ?? null

  const folders = useResource(kbId ? `folders:${kbId}` : null, () => listFolders(kbId as string))
  const folderList = folders.data ?? []
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null)
  const folderId = folderList.some((f) => f.id === selectedFolderId) ? selectedFolderId : null
  const [filesVersion, setFilesVersion] = useState(0)

  const sidebar = (
    <KnowledgeSidebar
      knowledgeBases={knowledgeBases}
      selectedKbId={kbId}
      onSelectKb={(id) => {
        setSelectedKbId(id)
        setSelectedFolderId(null)
        setNavOpen(false)
      }}
      folders={folders}
      selectedFolderId={folderId}
      onSelectFolder={(id) => {
        setSelectedFolderId(id)
        setView('files')
        setNavOpen(false)
      }}
      onFilesChanged={() => setFilesVersion((v) => v + 1)}
    />
  )

  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
        {!isMedium && (
          <Button size="icon-sm" variant="ghost" aria-label="打开知识库与目录" onClick={() => setNavOpen(true)}>
            <MenuIcon />
          </Button>
        )}
        <div className="flex min-w-0 items-center gap-2">
          <BrainCircuitIcon className="size-5" aria-hidden />
          <h1 className="truncate font-heading text-base font-semibold">KnowledgePilot</h1>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <HealthBadge />
          <UserSwitcher userId={userId} onChange={onSwitchUser} />
          {!isWide && (
            <Button
              size="icon-sm"
              variant="outline"
              aria-label="打开引用与工具轨迹"
              onClick={() => setPanelOpen(true)}
            >
              <PanelRightIcon />
            </Button>
          )}
        </div>
      </header>
      {banner && <div className="shrink-0 px-4 pt-3">{banner}</div>}

      <div className="flex min-h-0 flex-1">
        {isMedium && (
          <aside className="flex w-72 shrink-0 flex-col border-r p-4" aria-label="知识库与目录">
            {sidebar}
          </aside>
        )}
        <main className="flex min-w-0 flex-1 flex-col p-4">
          <Tabs
            value={view}
            onValueChange={(value) => setView(value as CenterView)}
            className="flex min-h-0 flex-1 flex-col gap-3"
          >
            <TabsList>
              <TabsTrigger value="files">
                <FilesIcon data-icon="inline-start" />
                文件
              </TabsTrigger>
              <TabsTrigger value="chat">
                <MessagesSquareIcon data-icon="inline-start" />
                对话
              </TabsTrigger>
            </TabsList>
            {/* Both views stay mounted so a streaming answer survives switching tabs. */}
            <TabsContent value="files" forceMount className="flex min-h-0 flex-col data-[state=inactive]:hidden">
              {kb ? (
                <FilesView
                  key={kb.id}
                  knowledgeBase={kb}
                  folders={folderList}
                  foldersKnown={Boolean(folders.data)}
                  folderId={folderId}
                  refreshToken={filesVersion}
                />
              ) : (
                <Empty className="border border-dashed">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <LibraryIcon />
                    </EmptyMedia>
                    <EmptyTitle>{knowledgeBases.loading ? '正在加载知识库…' : '请选择或创建知识库'}</EmptyTitle>
                    <EmptyDescription>知识库用于组织目录和文件，提问时可以选择一个或多个知识库。</EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </TabsContent>
            <TabsContent value="chat" forceMount className="flex min-h-0 flex-col data-[state=inactive]:hidden">
              <ChatView knowledgeBases={kbList} currentKbId={kbId} />
            </TabsContent>
          </Tabs>
        </main>
        {isWide && (
          <aside className="flex w-96 shrink-0 flex-col border-l p-4" aria-label="引用与工具轨迹">
            <InspectorPanel />
          </aside>
        )}
      </div>

      {!isMedium && (
        <Sheet open={navOpen} onOpenChange={setNavOpen}>
          <SheetContent side="left" className="flex w-80 flex-col p-4">
            <SheetHeader className="p-0">
              <SheetTitle>知识库与目录</SheetTitle>
              <SheetDescription className="sr-only">选择知识库和目录</SheetDescription>
            </SheetHeader>
            {sidebar}
          </SheetContent>
        </Sheet>
      )}
      {!isWide && (
        <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
          <SheetContent side="right" className="flex w-full flex-col p-4 sm:max-w-md">
            <SheetHeader className="p-0">
              <SheetTitle>引用与工具轨迹</SheetTitle>
              <SheetDescription className="sr-only">当前选中回答的引用和工具调用</SheetDescription>
            </SheetHeader>
            <InspectorPanel />
          </SheetContent>
        </Sheet>
      )}
      <DocumentViewer />
    </div>
  )
}
