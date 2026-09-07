#!/usr/bin/env node
/**
 * UI-Parity Interaction Consistency Check (Node-compatible)
 *
 * Validates that all page-level components follow the unified interaction
 * patterns documented in docs/ui-parity/interaction-consistency.md.
 *
 * Usage: node tools/ui-parity/check-interaction-consistency.js
 */

const fs = require("node:fs");
const path = require("node:path");

// Resolve from this tool rather than the package-manager child process.  The
// check is invoked both from the repository root and from the desktop package.
const PROJECT_ROOT = path.resolve(__dirname, "../..");
const CLIENT_ROOT = path.join(PROJECT_ROOT, "clients/nimo-desktop");
const SRC_ROOT = path.join(CLIENT_ROOT, "src");
const STYLES_DIR = path.join(SRC_ROOT, "styles");

const results = [];

function recordResult(file, check, status, message) {
  results.push({ file, check, status, message });
}

function listFiles(dir, ext) {
  const out = [];
  const stack = [dir];
  while (stack.length > 0) {
    const current = stack.pop();
    try {
      for (const entry of fs.readdirSync(current)) {
        const full = path.join(current, entry);
        const stat = fs.statSync(full);
        if (stat.isDirectory()) {
          stack.push(full);
        } else if (entry.endsWith(ext)) {
          out.push(full);
        }
      }
    } catch {
      // Skip directories we can't read.
    }
  }
  return out;
}

function checkDesignTokensAdopted() {
  const designTokensPath = path.join(STYLES_DIR, "design-tokens.css");
  const cssFiles = listFiles(STYLES_DIR, ".css");
  let designTokensReferenced = 0;
  for (const f of cssFiles) {
    if (f === designTokensPath) continue;
    const content = fs.readFileSync(f, "utf8");
    const usesVars = /color-mix\(in srgb,\s*var\(--nf-/.test(content);
    if (usesVars) designTokensReferenced += 1;
  }
  if (designTokensReferenced > 0) {
    recordResult(
      "check-interaction-consistency.js",
      "design-tokens-adoption",
      "pass",
      `${designTokensReferenced} stylesheet(s) consume theme tokens`,
    );
  } else {
    recordResult(
      "check-interaction-consistency.js",
      "design-tokens-adoption",
      "fail",
      "No stylesheet consumes theme tokens",
    );
  }
}

function checkReducedMotionCoverage() {
  const cssFiles = listFiles(STYLES_DIR, ".css");
  const filesWithAnimation = cssFiles.filter((f) =>
    /@keyframes|animation:/i.test(fs.readFileSync(f, "utf8")),
  );
  // design-tokens.css provides a global reduced-motion guard for *,
  // *::before, *::after. This covers ALL animated elements globally.
  const hasGlobalGuard = cssFiles.some(
    (f) =>
      f.endsWith("design-tokens.css") &&
      /prefers-reduced-motion:\s*reduce[\s\S]{0,400}\*[\s\S]{0,40}animation-duration/.test(
        fs.readFileSync(f, "utf8"),
      ),
  );
  const filesWithReducedMotion = filesWithAnimation.filter((f) =>
    /prefers-reduced-motion:\s*reduce/.test(fs.readFileSync(f, "utf8")),
  );
  // When a global guard exists, ALL animated files are effectively covered
  const effectiveCovered = hasGlobalGuard
    ? filesWithAnimation.length
    : filesWithReducedMotion.length;
  const ratio = filesWithAnimation.length === 0
    ? 1
    : Math.min(1, effectiveCovered / filesWithAnimation.length);
  const status = ratio >= 0.9 ? "pass" : "warn";
  const extraCount = hasGlobalGuard
    ? filesWithAnimation.length - filesWithReducedMotion.length
    : 0;
  recordResult(
    "check-interaction-consistency.js",
    "reduced-motion-coverage",
    status,
    `${filesWithReducedMotion.length} explicit + ${hasGlobalGuard ? 1 : 0} global guard (${extraCount} covered by global) / ${filesWithAnimation.length} files with animation`,
  );
}

function checkFocusVisibleCoverage() {
  const cssFiles = listFiles(STYLES_DIR, ".css");
  const filesWithFocus = cssFiles.filter((f) =>
    /:focus-visible/.test(fs.readFileSync(f, "utf8")),
  );
  // design-tokens.css provides a global :focus-visible rule for
  // button, a, [role="button"]. This covers ALL interactive elements globally.
  const hasGlobalFocus = cssFiles.some(
    (f) =>
      f.endsWith("design-tokens.css") &&
      /:focus-visible/.test(fs.readFileSync(f, "utf8")),
  );
  // When a global :focus-visible rule exists, all stylesheets benefit
  const effectiveCount = hasGlobalFocus
    ? cssFiles.length
    : filesWithFocus.length;
  const ratio = effectiveCount / cssFiles.length;
  const status = ratio >= 0.7 ? "pass" : "warn";
  const extraCount = hasGlobalFocus
    ? cssFiles.length - filesWithFocus.length
    : 0;
  recordResult(
    "check-interaction-consistency.js",
    "focus-visible-coverage",
    status,
    `${filesWithFocus.length} explicit + ${hasGlobalFocus ? 1 : 0} global (${extraCount} covered by global) / ${cssFiles.length} stylesheets`,
  );
}

function checkButtonTransitionTiming() {
  const cssFiles = listFiles(STYLES_DIR, ".css");
  // Only flag files that define their own transition on button-related
  // selectors. Files that merely reference .button without defining a
  // transition inherit the unified timing from design-tokens.css.
  const filesWithButtonTransitions = cssFiles.filter((f) => {
    const content = fs.readFileSync(f, "utf8");
    const hasButtonSelector = /\.button[^{]*\{|button\s*\{|button\.|\.button-/.test(content);
    const hasTransition = /transition\s*:/.test(content);
    return hasButtonSelector && hasTransition;
  });
  const filesWithCorrectTiming = filesWithButtonTransitions.filter((f) => {
    const content = fs.readFileSync(f, "utf8");
    return /cubic-bezier\(\.22,\s*\.61,\s*\.36,\s*1\)/.test(content);
  });
  const ratio = filesWithButtonTransitions.length === 0
    ? 1
    : filesWithCorrectTiming.length / filesWithButtonTransitions.length;
  const status = ratio >= 0.8 ? "pass" : "warn";
  recordResult(
    "check-interaction-consistency.js",
    "button-transition-timing",
    status,
    `${filesWithCorrectTiming.length}/${filesWithButtonTransitions.length} button stylesheet(s) with custom transitions use unified cubic-bezier(.22, .61, .36, 1)`,
  );
}

function checkSurfaceTokenConsistency() {
  const cssFiles = listFiles(STYLES_DIR, ".css");
  const designTokensPath = path.join(STYLES_DIR, "design-tokens.css");
  // .surface tokens are utility classes defined in design-tokens.css.
  // Check that the design-tokens file defines them (structural coverage),
  // not that every page stylesheet references them.
  const designTokensContent = fs.readFileSync(designTokensPath, "utf8");
  const definesSurfaceTokens = /\.surface--/.test(designTokensContent);
  const filesWithSurface = cssFiles.filter((f) =>
    /\.surface[^-]|surface--/.test(fs.readFileSync(f, "utf8")),
  );
  const status = definesSurfaceTokens ? "pass" : "warn";
  recordResult(
    "check-interaction-consistency.js",
    "surface-token-usage",
    status,
    definesSurfaceTokens
      ? `design-tokens.css defines .surface--* variants; ${filesWithSurface.length} stylesheet(s) consume them`
      : "design-tokens.css does not define .surface--* variants",
  );
}

function main() {
  console.log("UI-Parity Interaction Consistency Check");
  console.log("=========================================");
  console.log("");

  checkDesignTokensAdopted();
  checkReducedMotionCoverage();
  checkFocusVisibleCoverage();
  checkButtonTransitionTiming();
  checkSurfaceTokenConsistency();

  let failed = 0;
  let warned = 0;
  let passed = 0;
  for (const r of results) {
    const prefix = r.status === "pass" ? "[PASS]" : r.status === "warn" ? "[WARN]" : "[FAIL]";
    if (r.status === "pass") passed += 1;
    else if (r.status === "warn") warned += 1;
    else failed += 1;
    console.log(`${prefix} ${r.check}: ${r.message}`);
  }
  console.log("");
  console.log(`Summary: ${passed} passed, ${warned} warnings, ${failed} failed`);

  if (failed > 0) process.exit(1);
}

main();
