import { splitCitations } from './citations'

// Minimal mdast shapes; the full types live in @types/mdast, which is not a direct dependency.
interface MdNode {
  type: string
  value?: string
  children?: MdNode[]
  data?: { hName?: string; hProperties?: Record<string, unknown> }
}

/** hast tag the citation chip is rendered as; AnswerText maps it to a clickable badge. */
export const CITATION_TAG = 'cite'
/** hast property (React prop `data-citation`) that carries the marker, e.g. "c1". */
export const CITATION_PROPERTY = 'dataCitation'

// Inside these nodes a chip would be invalid (a button inside a link) or misleading (code).
const SKIP = new Set(['link', 'linkReference', 'inlineCode', 'code', 'html'])

function transform(node: MdNode, valid: Set<string>): void {
  if (!node.children || SKIP.has(node.type)) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type !== 'text' || !child.value?.includes('[c')) {
      transform(child, valid)
      next.push(child)
      continue
    }
    for (const segment of splitCitations(child.value, valid)) {
      if (segment.type === 'text') {
        next.push({ type: 'text', value: segment.text })
      } else {
        next.push({
          type: 'citationMarker',
          data: { hName: CITATION_TAG, hProperties: { [CITATION_PROPERTY]: segment.marker } },
          children: [{ type: 'text', value: segment.marker }],
        })
      }
    }
  }
  node.children = next
}

/**
 * Remark plugin: turns `[cN]` markers found in text (paragraphs, list items, emphasis, tables…)
 * into citation nodes when N is one of this message's citations. Unknown markers stay plain
 * text, as in splitCitations.
 */
export function remarkCitations(options: { markers: Iterable<string> }) {
  const valid = new Set(options.markers)
  return (tree: MdNode) => {
    transform(tree, valid)
  }
}
