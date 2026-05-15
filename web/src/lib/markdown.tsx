import { Fragment, type ReactNode } from "react";

/**
 * Minimal markdown → React tree renderer.
 *
 * Trust model: the markdown comes from server-side templates generated
 * by the orchestrator (``runs/<id>/merge_report.md``). We still build a
 * React tree directly so all text is rendered as text content; no raw
 * HTML is interpreted, which keeps the XSS surface minimal. Supported
 * subset: H1-H3 / fenced code blocks / unordered lists / paragraphs.
 */
export function renderMarkdown(md: string): ReactNode {
  const lines = md.split("\n");
  const nodes: ReactNode[] = [];
  let inCode = false;
  let codeBuf: string[] = [];
  let listBuf: string[] = [];

  const flushList = () => {
    if (listBuf.length === 0) return;
    nodes.push(
      <ul className="ml-5 list-disc space-y-0.5" key={`ul-${nodes.length}`}>
        {listBuf.map((item, i) => (
          <li key={i} className="text-slate-200 leading-relaxed">
            {item}
          </li>
        ))}
      </ul>,
    );
    listBuf = [];
  };

  for (const raw of lines) {
    if (raw.startsWith("```")) {
      flushList();
      if (inCode) {
        nodes.push(
          <pre
            key={`pre-${nodes.length}`}
            className="bg-slate-950 border border-slate-800 rounded p-2 overflow-x-auto text-[11px] my-2"
          >
            <code>{codeBuf.join("\n")}</code>
          </pre>,
        );
        codeBuf = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(raw);
      continue;
    }
    const line = raw.trimEnd();
    if (line.startsWith("### ")) {
      flushList();
      nodes.push(
        <h3
          key={`h3-${nodes.length}`}
          className="text-sm font-semibold text-slate-100 mt-3"
        >
          {line.slice(4)}
        </h3>,
      );
    } else if (line.startsWith("## ")) {
      flushList();
      nodes.push(
        <h2
          key={`h2-${nodes.length}`}
          className="text-base font-semibold text-slate-100 mt-4"
        >
          {line.slice(3)}
        </h2>,
      );
    } else if (line.startsWith("# ")) {
      flushList();
      nodes.push(
        <h1
          key={`h1-${nodes.length}`}
          className="text-lg font-semibold text-slate-50 mt-2"
        >
          {line.slice(2)}
        </h1>,
      );
    } else if (line.startsWith("- ")) {
      listBuf.push(line.slice(2));
    } else if (line === "") {
      flushList();
      // Blank lines act as paragraph separators — no DOM node needed.
    } else {
      flushList();
      nodes.push(
        <p
          key={`p-${nodes.length}`}
          className="text-slate-200 leading-relaxed"
        >
          {line}
        </p>,
      );
    }
  }
  if (inCode && codeBuf.length > 0) {
    nodes.push(
      <pre
        key={`pre-${nodes.length}`}
        className="bg-slate-950 border border-slate-800 rounded p-2 overflow-x-auto text-[11px] my-2"
      >
        <code>{codeBuf.join("\n")}</code>
      </pre>,
    );
  }
  flushList();
  return <Fragment>{nodes}</Fragment>;
}
