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

  it("does NOT interpret raw HTML — script tags render as text", () => {
    const { container } = render(
      <>{renderMarkdown("<script>alert(1)</script> some text")}</>,
    );
    // <script> tag should be in textContent (escaped via React), not as a real
    // element node.
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
});
