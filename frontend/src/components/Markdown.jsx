// Minimal renderer for the AI text: headings, bullets, **bold**, *italic* and `code`.
// Escapes HTML first, so model output can never inject markup.
export function Markdown({ text, testid = "ai-summary-text" }) {
  return (
    <div className="space-y-1.5 text-sm text-slate-700" data-testid={testid}>
      {(text || "").split("\n").map((line, i) => {
        const html = line
          .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/\*\*(.+?)\*\*/g, '<strong class="text-slate-900">$1</strong>')
          .replace(/(^|[^*\w])\*(\S[^*\n]*?)\*(?!\*)/g, "$1<em>$2</em>")
          .replace(/`(.+?)`/g, '<code class="font-mono text-xs bg-slate-100 px-1 rounded-sm">$1</code>');
        if (/^#{1,3}\s/.test(line))
          return <h4 key={i} className="text-sm font-semibold tracking-tight text-slate-900 pt-2"
            dangerouslySetInnerHTML={{ __html: html.replace(/^#{1,3}\s/, "") }} />;
        if (/^[-*]\s/.test(line))
          return <li key={i} className="ml-4 list-disc" dangerouslySetInnerHTML={{ __html: html.replace(/^[-*]\s/, "") }} />;
        if (!line.trim()) return <div key={i} className="h-1" />;
        return <p key={i} dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </div>
  );
}
