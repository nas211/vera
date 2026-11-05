import { parse } from "@babel/parser";
import { walk } from "estree-walker";
import MagicString from "magic-string";
import path from "node:path";
import type { Node } from "estree";
import type { Plugin } from "vite";

const VALID_EXTENSIONS = new Set([".jsx", ".tsx"]);

/** Minimal shape of the Babel JSX nodes this plugin reads (Babel JSX is not part of ESTree types). */
type JsxAttributeLike = { type: string; name?: { name?: unknown } };
type JsxOpeningElementLike = {
  type: "JSXOpeningElement";
  name?: { type?: string; name?: unknown; end?: number | null };
  attributes?: JsxAttributeLike[];
  loc?: { start?: { line: number; column: number } };
};

const isJsxOpeningElement = (n: unknown): n is JsxOpeningElementLike =>
  typeof n === "object" && n !== null && (n as { type?: unknown }).type === "JSXOpeningElement";

/**
 * Returns a Vite plugin that adds component data attributes for component selection.
 */
export default function componentTagger(): Plugin {
  return {
    name: "vite-plugin-component-tagger",
    apply: () => true,
    enforce: "pre",

    async transform(code: string, id: string) {
      try {
        // Ignore non-jsx files and files inside node_modules
        if (
          !VALID_EXTENSIONS.has(path.extname(id)) ||
          id.includes("node_modules")
        )
          return null;

        const ast = parse(code, {
          sourceType: "module",
          plugins: ["jsx", "typescript"],
        });

        const ms = new MagicString(code);
        const fileRelative = path.relative(process.cwd(), id);

        // Babel's File AST is walked structurally; estree-walker only needs `type`-tagged objects.
        walk(ast as unknown as Node, {
          enter(estreeNode) {
            try {
              const node: unknown = estreeNode;
              if (!isJsxOpeningElement(node)) return;

              // ── 1. Extract the tag / component name ──────────────────────────────
              if (!node.name || node.name.type !== "JSXIdentifier") return;
              const tagName = typeof node.name.name === "string" ? node.name.name : "";
              if (!tagName) return;

              // Skip certain HTML elements that are too generic
              const skipElements = new Set([
                "html",
                "head",
                "body",
                "script",
                "style",
                "meta",
                "link",
                "title",
              ]);
              if (skipElements.has(tagName.toLowerCase())) return;

              // ── 2. Check whether the tag already has data-component-id ───────────────
              const alreadyTagged = node.attributes?.some(
                (attr) =>
                  attr.type === "JSXAttribute" &&
                  attr.name?.name === "data-component-id",
              );
              if (alreadyTagged) return;

              // ── 3. Build the id "relative/file.jsx:line:column" ─────────────────
              const loc = node.loc?.start;
              if (!loc) return;
              const componentId = `${fileRelative}:${loc.line}:${loc.column}`;

              // ── 4. Inject the attributes just after the tag name ────────────────
              if (node.name.end != null) {
                ms.appendLeft(
                  node.name.end,
                  ` data-component-id="${componentId}" data-component-name="${tagName}"`,
                );
              }
            } catch (error) {
              console.warn(
                `[component-tagger] Warning: Failed to process JSX node in ${id}:`,
                error,
              );
            }
          },
        });

        // If nothing changed bail out.
        if (ms.toString() === code) return null;

        return {
          code: ms.toString(),
          map: ms.generateMap({ hires: true }),
        };
      } catch (error) {
        console.warn(
          `[component-tagger] Warning: Failed to transform ${id}:`,
          error,
        );
        return null;
      }
    },
  };
}
