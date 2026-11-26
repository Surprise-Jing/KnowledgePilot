import type { Components } from 'react-markdown'

import { Separator } from '@/components/ui/separator'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

// Styling for model-written Markdown. There is no typography plugin, so each element gets
// compact classes with semantic tokens. Raw HTML is never rendered (no rehype-raw).
export const markdownComponents: Components = {
  p: ({ node: _node, ...props }) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="my-2 flex list-disc flex-col gap-1 pl-5" {...props} />,
  ol: ({ node: _node, ...props }) => (
    <ol className="my-2 flex list-decimal flex-col gap-1 pl-5" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="pl-0.5 [&>ol]:my-1 [&>ul]:my-1" {...props} />,
  h1: ({ node: _node, ...props }) => <h3 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h2: ({ node: _node, ...props }) => <h3 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h3: ({ node: _node, ...props }) => <h4 className="mt-3 mb-1.5 font-semibold" {...props} />,
  h4: ({ node: _node, ...props }) => <h5 className="mt-2 mb-1 font-semibold" {...props} />,
  h5: ({ node: _node, ...props }) => <h6 className="mt-2 mb-1 font-medium" {...props} />,
  h6: ({ node: _node, ...props }) => <h6 className="mt-2 mb-1 font-medium" {...props} />,
  strong: ({ node: _node, ...props }) => <strong className="font-semibold" {...props} />,
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="my-2 border-l-2 pl-3 text-muted-foreground" {...props} />
  ),
  hr: () => <Separator className="my-3" />,
  a: ({ node: _node, href, ...props }) => (
    // react-markdown already drops unsafe protocols (javascript:, data:…) from href.
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="font-medium underline underline-offset-4"
      {...props}
    />
  ),
  // Remote images would be fetched as soon as the answer renders; show them as links instead.
  img: ({ src, alt }) =>
    typeof src === 'string' && src ? (
      <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="underline">
        {alt || src}
      </a>
    ) : (
      <span>{alt}</span>
    ),
  pre: ({ node: _node, ...props }) => (
    <pre
      className="my-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs [&>code]:bg-transparent [&>code]:p-0"
      {...props}
    />
  ),
  code: ({ node: _node, ...props }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]" {...props} />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-2 rounded-md border">
      <Table {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <TableHeader {...props} />,
  tbody: ({ node: _node, ...props }) => <TableBody {...props} />,
  tr: ({ node: _node, ...props }) => <TableRow {...props} />,
  th: ({ node: _node, ...props }) => <TableHead {...props} />,
  td: ({ node: _node, ...props }) => <TableCell className="whitespace-normal" {...props} />,
}
