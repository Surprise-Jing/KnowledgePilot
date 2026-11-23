import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TracePanel } from '@/features/trace/TracePanel'

import { CitationsPanel } from './CitationsPanel'
import { type InspectorTab, useInspector } from './inspector-context'

/** Right-hand panel: citations and tool trace of the selected answer. */
export function InspectorPanel() {
  const { tab, setTab, subject } = useInspector()
  const steps = subject?.liveTrace?.steps.length
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as InspectorTab)}
      className="flex min-h-0 flex-1 flex-col"
    >
      <TabsList className="w-full">
        <TabsTrigger value="citations">
          引用{subject && subject.citations.length > 0 ? `（${subject.citations.length}）` : ''}
        </TabsTrigger>
        <TabsTrigger value="trace">工具轨迹{steps ? `（${steps}）` : ''}</TabsTrigger>
      </TabsList>
      <TabsContent value="citations" className="min-h-0 flex-1 overflow-y-auto pt-2">
        <CitationsPanel />
      </TabsContent>
      <TabsContent value="trace" className="min-h-0 flex-1 overflow-y-auto pt-2">
        <TracePanel />
      </TabsContent>
    </Tabs>
  )
}
