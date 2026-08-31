#!/usr/bin/env node
/* ---------------------------------------------------------------------------
 * Offline frontend check.
 *
 * `npm run build` cannot run everywhere this repo gets worked on: esbuild ships
 * a platform-specific binary, so a node_modules installed on Windows has only
 * @esbuild/win32-x64 and Vite will not start under Linux (and vice versa). That
 * left no way to catch a typo in a JSX file without a full reinstall.
 *
 * @babel/parser is pure JavaScript and already present as a transitive
 * dependency, so it runs anywhere node does. This does four things Vite would
 * otherwise have caught:
 *
 *   1. Parses every .js/.jsx file. Syntax errors fail here.
 *   2. Resolves every relative import against the filesystem, with Vite's
 *      extension resolution. A renamed file or a bad path fails here — the
 *      single most common way this app has broken during a refactor.
 *   3. Checks that every default import actually has a default export, and that
 *      every named import exists. Vite does not error on a missing named export
 *      at build time; it yields `undefined` at runtime, which for a React
 *      component means "Element type is invalid" in the browser and nothing at
 *      all in the terminal.
 *   4. Cross-checks className strings in JSX against the selectors defined in
 *      styles.css, and reports classes that are used but never styled. This is
 *      a warning, not an error: dynamic classes and third-party classes are
 *      legitimate. It exists because the one failure mode a JS parser cannot see
 *      is a component that renders correctly and looks like nothing.
 *
 * This is NOT a substitute for `npm run build`. It does not typecheck, does not
 * run the bundler, and does not execute a single line of application code. It
 * catches the class of error that wastes the most time, and says so.
 *
 * Usage:  node tools/check_frontend.cjs
 * ------------------------------------------------------------------------- */

const fs = require("fs");
const path = require("path");

const parser = require("@babel/parser");

const FRONTEND_ROOT = path.resolve(__dirname, "..");
const SRC = path.join(FRONTEND_ROOT, "src");
const STYLESHEET = path.join(SRC, "styles.css");

/* Vite's resolution order for an extensionless relative import. */
const EXTENSIONS = ["", ".js", ".jsx", ".ts", ".tsx", ".json", ".mjs", ".cjs"];
const INDEX_FILES = ["index.js", "index.jsx", "index.ts", "index.tsx"];

const PARSE_OPTIONS = {
  sourceType: "module",
  plugins: ["jsx", "classProperties", "objectRestSpread", "optionalChaining", "nullishCoalescingOperator", "dynamicImport"]
};

const errors = [];
const warnings = [];

/* ---------------------------------------------------------------- discovery */

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      walk(full, out);
    } else if (/\.(jsx?|tsx?)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/* ------------------------------------------------------------------- parsing */

/** @returns {{ast: object, code: string} | null} */
function parseFile(file) {
  const code = fs.readFileSync(file, "utf8");
  try {
    return { ast: parser.parse(code, PARSE_OPTIONS), code };
  } catch (error) {
    // Babel's message already carries line:column.
    errors.push(`${rel(file)}: parse error — ${error.message}`);
    return null;
  }
}

function rel(file) {
  return path.relative(FRONTEND_ROOT, file).split(path.sep).join("/");
}

/* ----------------------------------------------------------------- resolving */

/**
 * Resolve a relative specifier the way Vite would.
 * @returns {string | null} absolute path, or null if nothing matches
 */
function resolveImport(fromFile, specifier) {
  const base = path.resolve(path.dirname(fromFile), specifier);

  for (const ext of EXTENSIONS) {
    const candidate = base + ext;
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  if (fs.existsSync(base) && fs.statSync(base).isDirectory()) {
    for (const name of INDEX_FILES) {
      const candidate = path.join(base, name);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return null;
}

/* ------------------------------------------------------------ export walking */

/**
 * Collect what a module exports.
 *
 * Deliberately shallow: it does not follow `export * from`, and records those
 * as a wildcard so the named-import check can skip a module it cannot fully
 * describe rather than reporting a false failure.
 */
function collectExports(ast) {
  const names = new Set();
  let hasDefault = false;
  let hasWildcard = false;

  for (const node of ast.program.body) {
    switch (node.type) {
      case "ExportDefaultDeclaration":
        hasDefault = true;
        break;

      case "ExportAllDeclaration":
        hasWildcard = true;
        break;

      case "ExportNamedDeclaration": {
        for (const spec of node.specifiers || []) {
          const exported = spec.exported.name || spec.exported.value;
          if (exported === "default") hasDefault = true;
          else names.add(exported);
        }
        const decl = node.declaration;
        if (!decl) break;
        if (decl.type === "VariableDeclaration") {
          for (const d of decl.declarations) collectPatternNames(d.id, names);
        } else if (decl.id) {
          names.add(decl.id.name);
        }
        break;
      }

      default:
        break;
    }
  }

  return { names, hasDefault, hasWildcard };
}

/** Destructuring in an export declaration: `export const {a, b} = x`. */
function collectPatternNames(pattern, out) {
  if (!pattern) return;
  switch (pattern.type) {
    case "Identifier":
      out.add(pattern.name);
      break;
    case "ObjectPattern":
      for (const prop of pattern.properties) {
        if (prop.type === "RestElement") collectPatternNames(prop.argument, out);
        else collectPatternNames(prop.value, out);
      }
      break;
    case "ArrayPattern":
      for (const el of pattern.elements) collectPatternNames(el, out);
      break;
    case "AssignmentPattern":
      collectPatternNames(pattern.left, out);
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------ import walking */

function collectImports(ast) {
  const out = [];
  for (const node of ast.program.body) {
    if (node.type !== "ImportDeclaration") continue;
    const source = node.source.value;
    const named = [];
    let wantsDefault = false;
    let wantsNamespace = false;

    for (const spec of node.specifiers) {
      if (spec.type === "ImportDefaultSpecifier") wantsDefault = true;
      else if (spec.type === "ImportNamespaceSpecifier") wantsNamespace = true;
      else named.push(spec.imported.name || spec.imported.value);
    }
    out.push({ source, named, wantsDefault, wantsNamespace, line: node.loc.start.line });
  }
  return out;
}

/* ------------------------------------------------- className / CSS crosscheck */

/**
 * Every className string literal in a file, including the static parts of
 * template literals and of `a ? "x" : "y"` expressions.
 *
 * Only literal text is collected. A class assembled from a variable is
 * invisible here, which is the correct trade: this check must not guess.
 */
function collectClassNames(ast, out) {
  walkAst(ast, (node) => {
    if (node.type !== "JSXAttribute") return;
    const name = node.name && node.name.name;
    if (name !== "className") return;
    const value = node.value;
    if (!value) return;

    if (value.type === "StringLiteral") {
      addClasses(value.value, out);
    } else if (value.type === "JSXExpressionContainer") {
      collectStringsIn(value.expression, out);
    }
  });
}

function collectStringsIn(node, out) {
  if (!node || typeof node !== "object") return;

  if (node.type === "StringLiteral") {
    addClasses(node.value, out);
    return;
  }

  if (node.type === "TemplateLiteral") {
    // Only whitespace-delimited tokens are whole classes. A quasi that ends
    // mid-token is a PREFIX (`tone-${x}` → "tone-"), and one that starts
    // mid-token is a SUFFIX (`${x}-panel` → "-panel"). Both are trimmed, or the
    // checker reports fragments that no stylesheet could ever contain.
    node.quasis.forEach((quasi, index) => {
      let text = quasi.value.cooked || "";
      const isFirst = index === 0;
      const isLast = index === node.quasis.length - 1;

      if (!isFirst && !/^\s/.test(text)) text = text.replace(/^\S+/, "");
      if (!isLast && !/\s$/.test(text)) text = text.replace(/\S+$/, "");

      addClasses(text, out);
    });
    for (const expr of node.expressions) collectStringsIn(expr, out);
    return;
  }

  // `cond ? "a" : "b"` — both branches are classes, but the TEST is not. Without
  // this, `tab !== "graph" ? ... : ...` reports "graph" as a missing class.
  if (node.type === "ConditionalExpression") {
    collectStringsIn(node.consequent, out);
    collectStringsIn(node.alternate, out);
    return;
  }

  // `flag && "a"` / `a || "b"` — the right side is a class. For && the left side
  // is a condition; for || it can be either, so both are taken there.
  if (node.type === "LogicalExpression") {
    if (node.operator !== "&&") collectStringsIn(node.left, out);
    collectStringsIn(node.right, out);
    return;
  }

  // Any other comparison is a condition, never a class.
  if (node.type === "BinaryExpression") return;

  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    const child = node[key];
    if (Array.isArray(child)) child.forEach((c) => collectStringsIn(c, out));
    else if (child && typeof child === "object" && child.type) collectStringsIn(child, out);
  }
}

function addClasses(text, out) {
  for (const token of String(text).split(/\s+/)) {
    if (token) out.add(token);
  }
}

function walkAst(node, visit) {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node);
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    const child = node[key];
    if (Array.isArray(child)) child.forEach((c) => walkAst(c, visit));
    else if (child && typeof child === "object" && child.type) walkAst(child, visit);
  }
}

/**
 * Class selectors defined in the stylesheet.
 *
 * Comments are stripped first, so a class mentioned only in a CSS comment does
 * not count as defined — that would defeat the point of the check.
 */
function collectCssClasses(cssPath) {
  if (!fs.existsSync(cssPath)) {
    warnings.push(`stylesheet not found at ${rel(cssPath)} — skipping class check`);
    return null;
  }
  const css = fs.readFileSync(cssPath, "utf8").replace(/\/\*[\s\S]*?\*\//g, " ");
  const found = new Set();
  const re = /\.(-?[_a-zA-Z][\w-]*)/g;
  let match;
  while ((match = re.exec(css)) !== null) found.add(match[1]);
  return found;
}

/* ------------------------------------------------------------------ the run */

function main() {
  const files = walk(SRC);
  if (files.length === 0) {
    console.error(`No source files under ${rel(SRC)}`);
    process.exit(1);
  }

  const modules = new Map();
  const usedClasses = new Set();

  // Pass 1: parse everything, collect exports and classNames.
  for (const file of files) {
    const parsed = parseFile(file);
    if (!parsed) continue;
    modules.set(file, {
      ast: parsed.ast,
      exports: collectExports(parsed.ast),
      imports: collectImports(parsed.ast)
    });
    collectClassNames(parsed.ast, usedClasses);
  }

  // Pass 2: resolve imports and check the shape of what they import.
  for (const [file, mod] of modules) {
    for (const imp of mod.imports) {
      if (!imp.source.startsWith(".")) continue;   // bare specifier: npm's problem

      const target = resolveImport(file, imp.source);
      if (!target) {
        errors.push(
          `${rel(file)}:${imp.line}: cannot resolve import "${imp.source}"`
        );
        continue;
      }

      // Non-JS assets (a .css or .svg import) have no exports to check.
      if (!modules.has(target)) continue;
      const targetExports = modules.get(target).exports;

      if (imp.wantsDefault && !targetExports.hasDefault) {
        errors.push(
          `${rel(file)}:${imp.line}: "${imp.source}" has no default export ` +
            `(resolved to ${rel(target)})`
        );
      }
      if (!targetExports.hasWildcard) {
        for (const name of imp.named) {
          if (!targetExports.names.has(name)) {
            errors.push(
              `${rel(file)}:${imp.line}: "${imp.source}" does not export "${name}" ` +
                `(resolved to ${rel(target)})`
            );
          }
        }
      }
    }
  }

  // Pass 3: classNames with no matching selector.
  const cssClasses = collectCssClasses(STYLESHEET);
  const unstyled = [];
  if (cssClasses) {
    for (const cls of [...usedClasses].sort()) {
      if (!cssClasses.has(cls)) unstyled.push(cls);
    }
  }

  /* -------------------------------------------------------------- reporting */

  console.log(`parsed ${modules.size}/${files.length} files under ${rel(SRC)}`);
  if (cssClasses) {
    console.log(
      `classNames: ${usedClasses.size} used, ${cssClasses.size} selectors defined in ${rel(STYLESHEET)}`
    );
  }

  if (unstyled.length > 0) {
    console.log(`\n${unstyled.length} className(s) with no selector in styles.css:`);
    for (const cls of unstyled) console.log(`  .${cls}`);
    console.log(
      "  (a warning, not a failure — but each one is either dead markup or an\n" +
        "   element rendering with no styling at all)"
    );
  }

  for (const warning of warnings) console.log(`warn: ${warning}`);

  if (errors.length > 0) {
    console.error(`\nFAIL — ${errors.length} error(s):`);
    for (const error of errors) console.error(`  ${error}`);
    console.error(
      "\nNote: this checks syntax, module resolution and export shape only.\n" +
        "It is not a substitute for `npm run build`."
    );
    process.exit(1);
  }

  console.log("\nOK — syntax, module resolution and export shape all clean.");
  console.log("Reminder: this is not a build. Run `npm run build` before deploying.");
}

main();
