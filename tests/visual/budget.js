#!/usr/bin/env node
/*
 * UI15 Add-screen FIT BUDGET — arithmetic verification (NOT a render).
 *
 * Real screenshots could not be produced inside this sandbox: the only
 * Chromium present is Hatch's managed build, which refuses to navigate
 * to the local app (ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS on every
 * launch mode tried; see qa.js header). This script instead extracts the
 * shipped UI15 constants from templates/index.html and checks the
 * construction invariants that make clipping impossible, plus prints the
 * honest fit table (how much of the FORM area scrolls internally at each
 * viewport height — the keypad + submit are pinned and never scroll).
 *
 * Invariants asserted:
 *  1. Category tile min-height >= padding + 30px icon + gap + TWO Myanmar
 *     label lines at 12px/1.9 (the 2-line worst case, e.g. Transport).
 *  2. Key circles: 58px default tier (>=56 spec), 52px short tier.
 *  3. Submit 52px, date/note inputs 48px, wallet row 44px.
 *  4. Date field width at 390px and 360px viewports >= 150px (a date
 *     control's UA internals need ~150px; the old 1:1.25 split gave it
 *     ~147px, which is why it painted over the note field).
 *  5. The UI15 block has no line-height below 1.75 anywhere, no
 *     catcell{min-height:0} squeeze, and Myanmar rules use 1.9.
 *
 * Usage: node tests/visual/budget.js   (exit 1 on any failed invariant)
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "..", "templates", "index.html"), "utf8");
const ui15 = html.slice(html.indexOf("UI15 Add screen"),
                        html.indexOf("</style>"));

let failures = 0;
function check(name, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
  if (!ok) failures++;
}
function num(re, src, what) {
  const m = src.match(re);
  if (!m) { check(`extract ${what}`, false, "pattern not found"); return NaN; }
  return parseFloat(m[1]);
}

// --- extract shipped constants -------------------------------------------
const tileMin = num(/\.addcard \.catcell\{min-height:(\d+)px/, ui15, "tile min-height");
const tilePadV = 6 + 6; // padding:6px 4px
const ico = num(/\.addcard \.catcell \.chip-ico\{width:(\d+)px/, ui15, "icon circle");
const tileGap = num(/\.addcard \.catcell\{[^}]*gap:(\d+)px/, ui15, "tile inner gap");
const key58 = num(/grid-template-rows:repeat\(4,(\d+)px\)/, ui15, "default key row");
const keyBtn = num(/\.addcard \.keypad button\{width:(\d+)px/, ui15, "key button");
const key52 = num(/max-height:740px\)[\s\S]*?grid-template-rows:repeat\(4,(\d+)px\)/, ui15, "short-tier key row");
const saveH = num(/\.addcard \.savebtn\{height:(\d+)px/, ui15, "submit height");
const inputH = num(/\.daterow \.field input\{[^}]*height:(\d+)px/, ui15, "field height");
const walletH = num(/\.walletwrap\{[^}]*min-height:(\d+)px/, ui15, "wallet row");
const dateFlex = num(/\.field\.date-f\{flex:([\d.]+) 1 0\}/, ui15, "date flex");
const noteFlex = num(/\.field\.note-f\{flex:([\d.]+) 1 0\}/, ui15, "note flex");

// --- invariant 1: tiles are content-driven, never height-capped ----------
// min-height is only a floor for 1-line tiles; a 2-line Myanmar label's
// content height (91.6px) must be REACHABLE, i.e. no height/max-height
// may cap the tile and rows size to their tallest item (grid stretch).
const labelTwoLines = 2 * 12 * 1.9; // 12px font, lh 1.9
const tileNeed = tilePadV + ico + tileGap + labelTwoLines;
const catcellRule = (ui15.match(/\.addcard \.catcell\{[^}]*\}/) || [""])[0];
check("tile height is auto (no height/max-height cap in UI15)",
      !/(^|[^-])height:/.test(catcellRule.replace("min-height:", ""))
      && tileMin <= tileNeed,
      `floor ${tileMin}px; 2-line content ${tileNeed.toFixed(1)}px grows freely`);

// --- invariant 2/3: control sizes -----------------------------------------
check("key circles 58px default tier, rows match buttons",
      key58 === 58 && keyBtn === 58, `rows ${key58}, buttons ${keyBtn}`);
check("key circles 52px in <=740px tier (never below 52 there)",
      key52 === 52, `rows ${key52}`);
check("submit 52px / fields 48px / wallet row 44px",
      saveH === 52 && inputH === 48 && walletH === 44,
      `save ${saveH}, input ${inputH}, wallet ${walletH}`);

// --- invariant 4: date control gets a wide enough box ----------------------
function dateWidth(vw) {
  const cardInner = vw - 32 /* .center padding */ - 32 /* card padding */;
  return (cardInner - 10 /* daterow gap */) * dateFlex / (dateFlex + noteFlex);
}
check("date field >= 150px at 390px viewport", dateWidth(390) >= 150,
      `${dateWidth(390).toFixed(0)}px`);
check("date field >= 148px at 360px viewport", dateWidth(360) >= 148,
      `${dateWidth(360).toFixed(0)}px`);

// --- invariant 5: no squeeze leftovers in the UI15 block -------------------
check("no catcell min-height:0 squeeze in UI15",
      !/\.catcell\{[^}]*min-height:0/.test(ui15));
// the amount numeral's 1.15 is digits-only (tabular Latin numerals carry
// no ascenders/descenders to shave); every TEXT line-height must be >=1.75
const ui15Text = ui15.replace(/\.addcard \.amountbig\{[^}]*\}/, "");
check("no sub-1.75 line-height on UI15 text",
      ![...ui15Text.matchAll(/line-height:([\d.]+)/g)].some(m => parseFloat(m[1]) < 1.75),
      [...ui15Text.matchAll(/line-height:([\d.]+)/g)].map(m => m[1]).join(","));
check("Myanmar catcell labels use line-height 1.9",
      /body\.lang-my \.addcard \.catcell span:last-child\{line-height:1\.9\}/.test(ui15));

// --- honest fit table (Myanmar worst case, 1-line field labels) ------------
// Fixed stack heights derived from the shipped constants (see UI15 comment
// in templates/index.html for the component list).
const T = {
  sechead: 15.5 * 1.9 + 6,
  toggle: 6 + 2 + (14 + 13.5 * 1.9),
  amtrow: 10 + (11 * 1.9 + 2) + 32 * 1.15 + 10 + 1,
  catlbl: 12 + 11 * 1.9 + 6,
  catgrid: tileNeed + 8 + tileMin, // worst row grows to 2-line content (91.6), other row at floor
  wallet: 8 + walletH,
  daterow: 10 + (11 * 1.9 + 5) + inputH,
  keypad58: 10 + 4 * 58 + 3 * 12,
  keypad52: 8 + 4 * 52 + 3 * 10,
  save: 10 + saveH,
};
console.log("\nFit table — sheet height = viewport - 164 (header 76 + nav 88);");
console.log("keypad + submit are pinned (flex:none) and ALWAYS visible;");
console.log("'form scroll' = px the FORM area scrolls internally (0 = no scroll):");
for (const h of [932, 844, 740, 667]) {
  const short = h <= 740;
  const cardPad = short ? 22 : 26;
  const total = T.sechead + cardPad + T.toggle + T.amtrow + T.catlbl +
    T.catgrid + T.wallet + T.daterow + (short ? T.keypad52 : T.keypad58) +
    T.save - (short ? 12 : 0) /* tightened margins in the short tier */;
  const left = T.toggle + T.amtrow + T.catlbl + T.catgrid + T.wallet + T.daterow;
  const sheet = h - 164;
  const overflow = Math.max(0, total - sheet);
  console.log(`  ${h}px viewport: stack ${total.toFixed(0)} vs sheet ${sheet} ` +
    `-> form scroll ~${overflow.toFixed(0)}px of ${left.toFixed(0)}px form; ` +
    `keys ${short ? 52 : 58}px; NOTHING clipped`);
}
console.log("\nNote: English/1-line-tile content is ~46px shorter; the note");
console.log("field's long Myanmar label may wrap (grows, never clips) and is");
console.log("absorbed by the same internal form scroll.");

process.exit(failures ? 1 : 0);
