import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders H1/H2/H3 with correct text content", () => {
    const { container } = render(<>{renderMarkdown("# Title\n## Sub\n### Detail")}</>);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("h2")?.textContent).toBe("Sub");
    expect(container.querySelector("h3")?.textContent).toBe("Detail");
  });

  it("renders fenced code blocks as <pre><code>", () => {
    const md = "```\nconst x = 1;\nconst y = 2;\n```";
    const { container } = render(<>{renderMarkdown(md)}</>);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("const x = 1;");
    expect(pre?.textContent).toContain("const y = 2;");
  });

  it("renders unordered list items as <li>", () => {
    const { container } = render(<>{renderMarkdown("- first\n- second\n- third")}</>);
    const items = container.querySelectorAll("li");
    expect(items.length).toBe(3);
    expect(items[0].textContent).toBe("first");
    expect(items[2].textContent).toBe("third");
  });

  it("renders ordered list items as <ol><li>", () => {
    const { container } = render(<>{renderMarkdown("1. one\n2. two\n3. three")}</>);
    const ol = container.querySelector("ol");
    expect(ol).not.toBeNull();
    expect(ol?.querySelectorAll("li").length).toBe(3);
  });

  it("does NOT interpret raw HTML — script tags render as text", () => {
    const { container } = render(
      <>{renderMarkdown("<script>alert(1)</script> some text")}</>,
    );
    expect(container.querySelectorAll("script").length).toBe(0);
    expect(container.textContent).toContain("<script>");
  });

  it("treats blank lines as separators without producing extra DOM nodes", () => {
    const { container } = render(<>{renderMarkdown("para 1\n\npara 2")}</>);
    const paragraphs = container.querySelectorAll("p");
    expect(paragraphs.length).toBe(2);
  });

  it("flushes a list before a heading", () => {
    const md = "- a\n- b\n## After";
    const { container } = render(<>{renderMarkdown(md)}</>);
    const ul = container.querySelector("ul");
    const h2 = container.querySelector("h2");
    expect(ul?.children.length).toBe(2);
    expect(h2?.textContent).toBe("After");
  });

  it("renders a GFM table with header + body cells", () => {
    const md = [
      "| Feature | Files | Status |",
      "|---------|-------|--------|",
      "| auth | `auth.go` | PASS |",
      "| login | `login.go` | FAIL |",
    ].join("\n");
    const { container } = render(<>{renderMarkdown(md)}</>);
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    const ths = container.querySelectorAll("th");
    expect(ths.length).toBe(3);
    expect(ths[0].textContent).toBe("Feature");
    const rows = container.querySelectorAll("tbody tr");
    expect(rows.length).toBe(2);
    expect(rows[0].querySelectorAll("td")[0].textContent).toBe("auth");
    // Inline code inside table cell still renders as <code>.
    expect(rows[0].querySelector("td code")?.textContent).toBe("auth.go");
    // Wrapper for horizontal scrolling.
    expect(container.querySelector(".table-wrap")).not.toBeNull();
  });

  it("honours :--- alignment markers in table separator", () => {
    const md = [
      "| L | C | R |",
      "|:--|:-:|--:|",
      "| a | b | c |",
    ].join("\n");
    const { container } = render(<>{renderMarkdown(md)}</>);
    const ths = container.querySelectorAll("th");
    expect((ths[0] as HTMLElement).style.textAlign).toBe("left");
    expect((ths[1] as HTMLElement).style.textAlign).toBe("center");
    expect((ths[2] as HTMLElement).style.textAlign).toBe("right");
  });

  it("does NOT treat a single `-` separator (list-like) as a table", () => {
    // ``- foo`` is a list, not a table header even though the next
    // line contains dashes.
    const md = "- foo\n- bar";
    const { container } = render(<>{renderMarkdown(md)}</>);
    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelectorAll("li").length).toBe(2);
  });

  it("renders bold and italic inline markers", () => {
    const { container } = render(
      <>{renderMarkdown("**Status**: ok and *italic* word")}</>,
    );
    expect(container.querySelector("strong")?.textContent).toBe("Status");
    expect(container.querySelector("em")?.textContent).toBe("italic");
  });

  it("renders inline code with backticks", () => {
    const { container } = render(
      <>{renderMarkdown("see `origin/forgejo` branch")}</>,
    );
    expect(container.querySelector("code")?.textContent).toBe("origin/forgejo");
  });

  it("renders safe http links and ignores javascript: URLs", () => {
    const md =
      "Visit [docs](https://example.com/x) but [evil](javascript:alert(1))";
    const { container } = render(<>{renderMarkdown(md)}</>);
    const safe = container.querySelector("a[href='https://example.com/x']");
    expect(safe).not.toBeNull();
    expect(safe?.getAttribute("target")).toBe("_blank");
    expect(safe?.getAttribute("rel")).toContain("noopener");
    // The javascript: link must not become an <a>; its source text
    // survives in the rendered output as literal characters.
    expect(container.querySelectorAll("a").length).toBe(1);
    expect(container.textContent).toContain("javascript:alert(1)");
  });

  it("renders a horizontal rule for --- on its own line", () => {
    const { container } = render(<>{renderMarkdown("para\n\n---\n\nmore")}</>);
    expect(container.querySelector("hr")).not.toBeNull();
  });
});
