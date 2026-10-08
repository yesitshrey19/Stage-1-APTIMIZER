// Minimal renderer for the AI text: headings, bullets, tables, rules, **bold**, *italic*
// and `code`. Escapes HTML first, so model output can never inject markup.

function inline(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-slate-900">$1</strong>')
    .replace(/(^|[^*\w])\*(\S[^*\n]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`(.+?)`/g, '<code class="font-mono text-xs bg-slate-100 px-1 rounded-sm">$1</code>');
}

const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
const isTableDivider = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function Markdown({ text, testid = "ai-summary-text" }) {
  const lines = (text || "").split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    // A markdown table: collect consecutive |...| rows, drop the ---|--- divider.
    if (isTableRow(line)) {
      const rows = [];
      while (i < lines.length && (isTableRow(lines[i]) || isTableDivider(lines[i]))) {
        if (!isTableDivider(lines[i])) rows.push(cells(lines[i]));
        i += 1;
      }
      i -= 1;
      const [head, ...body] = rows;
      out.push(
        <div key={`t${i}`} className="overflow-x-auto my-1">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr>{head.map((c, j) => (
                <th key={j} className="border border-slate-200 bg-slate-50 px-2 py-1 text-left font-semibold text-slate-900"
                  dangerouslySetInnerHTML={{ __html: inline(c) }} />
              ))}</tr>
            </thead>
            <tbody>
              {body.map((r, k) => (
                <tr key={k}>{r.map((c, j) => (
                  <td key={j} className="border border-slate-200 px-2 py-1 align-top"
                    dangerouslySetInnerHTML={{ __html: inline(c) }} />
                ))}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push(<hr key={i} className="border-slate-200 my-1" />);
      continue;
    }
    const html = inline(line);
    if (/^#{1,4}\s/.test(line)) {
      out.push(<h4 key={i} className="text-sm font-semibold tracking-tight text-slate-900 pt-2"
        dangerouslySetInnerHTML={{ __html: html.replace(/^#{1,4}\s/, "") }} />);
    } else if (/^\s*[-*]\s/.test(line)) {
      out.push(<li key={i} className="ml-4 list-disc"
        dangerouslySetInnerHTML={{ __html: html.replace(/^\s*[-*]\s/, "") }} />);
    } else if (/^\s*\d+[.)]\s/.test(line)) {
      out.push(<li key={i} className="ml-4 list-decimal"
        dangerouslySetInnerHTML={{ __html: html.replace(/^\s*\d+[.)]\s/, "") }} />);
    } else if (!line.trim()) {
      out.push(<div key={i} className="h-1" />);
    } else {
      out.push(<p key={i} dangerouslySetInnerHTML={{ __html: html }} />);
    }
  }
  return <div className="space-y-1.5 text-sm text-slate-700" data-testid={testid}>{out}</div>;
}
