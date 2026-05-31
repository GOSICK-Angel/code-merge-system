import { Fragment, type ReactNode } from "react";

/**
 * Minimal markdown to React tree renderer.
 *
 * Trust model: the markdown comes from server-side templates generated
 * by the orchestrator (runs/<id>/merge_report.md). We build a React
 * tree directly so all text renders as text content; no raw HTML is
 * interpreted, which keeps the XSS surface minimal even though the
 * source is technically first-party.
 *
 * Supported subset:
 *   Block:  H1-H3, fenced code, unordered/ordered lists, GFM tables
 *           (header + |---| separator + body rows), horizontal rule
 *           (--- on its own line), paragraphs.
 *   Inline: **bold**, *italic* / _italic_, `code`, [label](url) where
 *           url must start with http(s):// / mailto: / / / # so a
 *           malicious javascript: link renders as plain text.
 */

const SAFE_URL = /^(https?:\/\/|mailto:|\/|#)/i;

let inlineCounter = 0;
function inlineKey(prefix: string): string {
  inlineCounter += 1;
  return `${prefix}-${inlineCounter}`;
}

export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let buf = "";
  const flushBuf = (): void => {
    if (buf) {
      nodes.push(buf);
      buf = "";
    }
  };

  let i = 0;
  while (i < text.length) {
    const ch = text[i];

    if (ch === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        flushBuf();
        nodes.push(<code key={inlineKey("c")}>{text.slice(i + 1, end)}</code>);
        i = end + 1;
        continue;
      }
    }

    if (ch === "*" && text[i + 1] === "*") {
      const end = text.indexOf("**", i + 2);
      if (end > i + 2) {
        flushBuf();
        nodes.push(
          <strong key={inlineKey("b")}>
            {renderInline(text.slice(i + 2, end))}
          </strong>,
        );
        i = end + 2;
        continue;
      }
    }

    if (ch === "*" || ch === "_") {
      const end = text.indexOf(ch, i + 1);
      if (
        end > i + 1 &&
        text[i + 1] !== " " &&
        text[end - 1] !== " "
      ) {
        flushBuf();
        nodes.push(
          <em key={inlineKey("i")}>
            {renderInline(text.slice(i + 1, end))}
          </em>,
        );
        i = end + 1;
        continue;
      }
    }

    if (ch === "[") {
      const close = text.indexOf("]", i + 1);
      if (close > i && text[close + 1] === "(") {
        const urlEnd = text.indexOf(")", close + 2);
        if (urlEnd > close + 1) {
          const url = text.slice(close + 2, urlEnd).trim();
          if (SAFE_URL.test(url)) {
            const label = text.slice(i + 1, close);
            flushBuf();
            nodes.push(
              <a
                key={inlineKey("a")}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {renderInline(label)}
              </a>,
            );
            i = urlEnd + 1;
            continue;
          }
        }
      }
    }

    buf += ch;
    i += 1;
  }

  flushBuf();
  return nodes;
}

interface TableBlock {
  header: string[];
  align: ("left" | "center" | "right" | null)[];
  rows: string[][];
  consumed: number;
}

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((c) => c.trim());
}

function parseAlign(sepCells: string[]): TableBlock["align"] {
  return sepCells.map((cell) => {
    const c = cell.trim();
    const left = c.startsWith(":");
    const right = c.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return null;
  });
}

function parseTable(lines: string[], start: number): TableBlock | null {
  const header = lines[start];
  const sep = lines[start + 1];
  if (!header || !sep) return null;
  if (!header.includes("|")) return null;
  if (!sep.includes("|") || !/^[\s|:\-]+$/.test(sep) || !sep.includes("-")) {
    return null;
  }
  const headerCells = splitRow(header);
  const sepCells = splitRow(sep);
  if (sepCells.length !== headerCells.length) return null;
  const align = parseAlign(sepCells);

  const rows: string[][] = [];
  let i = start + 2;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.includes("|") || !line.trim()) break;
    rows.push(splitRow(line));
    i += 1;
  }
  return { header: headerCells, align, rows, consumed: i - start };
}

export function renderMarkdown(md: string): ReactNode {
  inlineCounter = 0;

  const lines = md.split("\n");
  const nodes: ReactNode[] = [];
  let inCode = false;
  let codeBuf: string[] = [];

  let listBuf: string[] = [];
  let listOrdered = false;

  const flushList = (): void => {
    if (listBuf.length === 0) return;
    const items = listBuf.map((item, i) => (
      <li key={i}>{renderInline(item)}</li>
    ));
    nodes.push(
      listOrdered ? (
        <ol key={`ol-${nodes.length}`}>{items}</ol>
      ) : (
        <ul key={`ul-${nodes.length}`}>{items}</ul>
      ),
    );
    listBuf = [];
    listOrdered = false;
  };

  for (let idx = 0; idx < lines.length; idx += 1) {
    const raw = lines[idx];

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
      nodes.push(
        <h3 key={`h3-${nodes.length}`}>{renderInline(line.slice(4))}</h3>,
      );
      continue;
    }
    if (line.startsWith("## ")) {
      flushList();
      nodes.push(
        <h2 key={`h2-${nodes.length}`}>{renderInline(line.slice(3))}</h2>,
      );
      continue;
    }
    if (line.startsWith("# ")) {
      flushList();
      nodes.push(
        <h1 key={`h1-${nodes.length}`}>{renderInline(line.slice(2))}</h1>,
      );
      continue;
    }

    if (/^---+\s*$/.test(line) || /^\*\*\*+\s*$/.test(line)) {
      flushList();
      nodes.push(<hr key={`hr-${nodes.length}`} />);
      continue;
    }

    const ulMatch = /^[-*+]\s+(.*)$/.exec(line);
    if (ulMatch) {
      if (listOrdered) flushList();
      listBuf.push(ulMatch[1]);
      continue;
    }
    const olMatch = /^(\d+)\.\s+(.*)$/.exec(line);
    if (olMatch) {
      if (!listOrdered && listBuf.length > 0) flushList();
      listOrdered = true;
      listBuf.push(olMatch[2]);
      continue;
    }

    if (line === "") {
      flushList();
      continue;
    }

    if (line.includes("|")) {
      const table = parseTable(lines, idx);
      if (table) {
        flushList();
        nodes.push(
          <div key={`tw-${nodes.length}`} className="table-wrap">
            <table>
              <thead>
                <tr>
                  {table.header.map((h, j) => (
                    <th
                      key={j}
                      style={
                        table.align[j]
                          ? { textAlign: table.align[j]! }
                          : undefined
                      }
                    >
                      {renderInline(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td
                        key={c}
                        style={
                          table.align[c]
                            ? { textAlign: table.align[c]! }
                            : undefined
                        }
                      >
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
        idx += table.consumed - 1;
        continue;
      }
    }

    flushList();
    nodes.push(<p key={`p-${nodes.length}`}>{renderInline(line)}</p>);
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
