import { Fragment, type ReactNode } from "react";

/**
 * Minimal markdown → React tree renderer.
 *
 * Trust model: the markdown comes from server-side templates generated
 * by the orchestrator (``runs/<id>/merge_report.md``). We build a React
 * tree directly so all text renders as text content; no raw HTML is
 * interpreted, which keeps the XSS surface minimal.
 *
 * Element styling is delegated to the enclosing ``.md`` container in
 * ``index.css`` — we emit plain semantic tags here so the brutalist
 * theme owns the look. Supported subset: H1-H3 / fenced code blocks /
 * unordered lists / paragraphs.
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
      <ul key={`ul-${nodes.length}`}>
        {listBuf.map((item, i) => (
          <li key={i}>{item}</li>
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
          <pre key={`pre-${nodes.length}`}>
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
      nodes.push(<h3 key={`h3-${nodes.length}`}>{line.slice(4)}</h3>);
    } else if (line.startsWith("## ")) {
      flushList();
      nodes.push(<h2 key={`h2-${nodes.length}`}>{line.slice(3)}</h2>);
    } else if (line.startsWith("# ")) {
      flushList();
      nodes.push(<h1 key={`h1-${nodes.length}`}>{line.slice(2)}</h1>);
    } else if (line.startsWith("- ")) {
      listBuf.push(line.slice(2));
    } else if (line === "") {
      flushList();
    } else {
      flushList();
      nodes.push(<p key={`p-${nodes.length}`}>{line}</p>);
    }
  }
  if (inCode && codeBuf.length > 0) {
    nodes.push(
      <pre key={`pre-${nodes.length}`}>
        <code>{codeBuf.join("\n")}</code>
      </pre>,
    );
  }
  flushList();
  return <Fragment>{nodes}</Fragment>;
}
