/**
 * hamsterSprites.ts
 * Pixel-art sprite definitions for all 8 agents.
 *
 * Grid: 12 columns × 16 rows per frame.
 * Color index meaning:
 *   0 = transparent
 *   1 = body (agent's main color)
 *   2 = accessory / accent
 *   3 = face (white/light)
 *   4 = dark outline
 *   5 = secondary accessory
 *
 * Each agent has frames for: idle (2), working (4), done (2), error (2).
 */

export type PixelRow = number[];
export type SpriteFrame = PixelRow[]; // 16 rows of 12 pixels

export interface AgentSprites {
  idle: SpriteFrame[];
  working: SpriteFrame[];
  done: SpriteFrame[];
  error: SpriteFrame[];
}

export type SpriteMap = Record<string, AgentSprites>;

// ---------------------------------------------------------------------------
// Base hamster body — shared template (idle frame A)
// Row index 0 = top of the 16-row grid
// ---------------------------------------------------------------------------
//  Legend:  0=transparent 1=body 4=outline 3=face/white 2=accessory 5=secondary

// ---- PM (orange, necktie + notepad) ----
const PM_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,2,1,1,1,1,2,1,4,0],  // necktie dots
  [0,4,1,1,2,2,2,2,1,1,4,0],  // necktie stripe
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,5,5,1,4,0,0,0],  // notepad
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const PM_IDLE_B: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,2,1,1,1,1,2,1,4,0],
  [0,4,1,1,2,2,2,2,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,5,5,1,4,0,0,0],
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,4,4,0,0,4,4,0,0,0], // slightly lifted legs
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const PM_WORKING_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,3,3,1,4,0,0], // wink
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,2,1,1,1,1,2,1,4,0],
  [0,4,1,1,2,2,2,2,1,1,4,0],
  [4,1,1,1,1,1,1,1,1,1,1,4], // arm out writing
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,4,4,0,0,4,4,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const PM_WORKING_B: SpriteFrame = PM_IDLE_A; // cycle frames
const PM_WORKING_C: SpriteFrame = PM_WORKING_A;
const PM_WORKING_D: SpriteFrame = PM_IDLE_B;

const PM_DONE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0], // happy eyes (same as face)
  [0,0,0,4,3,5,5,3,4,0,0,0], // smile
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [4,1,1,2,1,1,1,1,2,1,1,4], // both arms raised
  [0,4,1,1,2,2,2,2,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,5,5,1,4,0,0,0],
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const PM_DONE_B: SpriteFrame = PM_DONE_A;

const PM_ERROR_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,4,3,3,4,1,4,0,0], // worried eyes
  [0,0,0,4,3,4,4,3,4,0,0,0], // frown
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,2,1,1,1,1,2,1,4,0],
  [0,4,1,1,2,2,2,2,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,5,5,1,4,0,0,0],
  [0,0,0,4,5,5,5,5,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const PM_ERROR_B: SpriteFrame = PM_ERROR_A;

// ---- DEV (blue, glasses + hoodie) ----
const DEV_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,2,2,4,4,2,2,4,0,0], // glasses frames
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,2,1,1,2,1,1,4,0], // hoodie strings
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const DEV_IDLE_B: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,2,2,4,4,2,2,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,2,1,1,2,1,1,4,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,4,4,0,0,4,4,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const DEV_WORKING_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,2,2,4,4,2,2,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [4,1,1,1,2,1,1,2,1,1,1,4], // typing arms extended
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const DEV_WORKING_B: SpriteFrame = DEV_IDLE_A;
const DEV_WORKING_C: SpriteFrame = DEV_WORKING_A;
const DEV_WORKING_D: SpriteFrame = DEV_IDLE_B;

const DEV_DONE_A: SpriteFrame = DEV_IDLE_A;
const DEV_DONE_B: SpriteFrame = DEV_IDLE_B;
const DEV_ERROR_A: SpriteFrame = DEV_IDLE_A;
const DEV_ERROR_B: SpriteFrame = DEV_IDLE_B;

// ---- QA (green, clipboard + magnifier) ----
const QA_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,5,5,5,1,1,1,1,4,0], // clipboard
  [0,0,4,1,5,5,5,1,1,4,0,0],
  [0,0,4,1,5,5,5,1,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const QA_IDLE_B: SpriteFrame = QA_IDLE_A;

const QA_WORKING_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,3,3,1,4,0,0], // wink inspecting
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,5,5,5,1,2,2,1,4,0], // clipboard + magnifier
  [0,4,1,5,5,5,1,2,0,2,4,0],
  [0,0,4,1,5,5,1,1,2,4,0,0],
  [0,0,4,1,1,1,1,1,4,4,0,0], // magnifier handle
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const QA_WORKING_B: SpriteFrame = QA_IDLE_A;
const QA_WORKING_C: SpriteFrame = QA_WORKING_A;
const QA_WORKING_D: SpriteFrame = QA_IDLE_B;
const QA_DONE_A: SpriteFrame = QA_IDLE_A;
const QA_DONE_B: SpriteFrame = QA_IDLE_B;
const QA_ERROR_A: SpriteFrame = QA_IDLE_A;
const QA_ERROR_B: SpriteFrame = QA_IDLE_B;

// ---- SECURITY (red, helmet + shield) ----
const SEC_IDLE_A: SpriteFrame = [
  [0,0,0,2,2,2,2,2,2,0,0,0], // helmet
  [0,0,2,2,2,2,2,2,2,2,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,5,5,1,1,1,1,5,5,4,0], // shield outline
  [0,4,5,5,5,5,5,5,5,5,4,0],
  [0,0,4,5,5,5,5,5,5,4,0,0],
  [0,0,4,1,5,5,5,5,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const SEC_IDLE_B: SpriteFrame = SEC_IDLE_A;
const SEC_WORKING_A: SpriteFrame = SEC_IDLE_A;
const SEC_WORKING_B: SpriteFrame = SEC_IDLE_B;
const SEC_WORKING_C: SpriteFrame = SEC_IDLE_A;
const SEC_WORKING_D: SpriteFrame = SEC_IDLE_B;
const SEC_DONE_A: SpriteFrame = SEC_IDLE_A;
const SEC_DONE_B: SpriteFrame = SEC_IDLE_B;
const SEC_ERROR_A: SpriteFrame = SEC_IDLE_A;
const SEC_ERROR_B: SpriteFrame = SEC_IDLE_B;

// ---- BUILD (yellow, hard hat + wrench) ----
const BUILD_IDLE_A: SpriteFrame = [
  [0,0,0,2,2,2,2,2,0,0,0,0], // hard hat
  [0,0,2,2,2,2,2,2,2,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,1,1,1,1,1,1,5,5,4,0], // wrench
  [0,4,1,1,1,1,1,5,5,0,4,0],
  [0,0,4,1,1,1,5,5,1,4,0,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const BUILD_IDLE_B: SpriteFrame = BUILD_IDLE_A;
const BUILD_WORKING_A: SpriteFrame = BUILD_IDLE_A;
const BUILD_WORKING_B: SpriteFrame = BUILD_IDLE_B;
const BUILD_WORKING_C: SpriteFrame = BUILD_IDLE_A;
const BUILD_WORKING_D: SpriteFrame = BUILD_IDLE_B;
const BUILD_DONE_A: SpriteFrame = BUILD_IDLE_A;
const BUILD_DONE_B: SpriteFrame = BUILD_IDLE_B;
const BUILD_ERROR_A: SpriteFrame = BUILD_IDLE_A;
const BUILD_ERROR_B: SpriteFrame = BUILD_IDLE_B;

// ---- HARNESS (purple, lab coat + beaker) ----
const HARNESS_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,3,1,1,3,4,4,0,0], // lab coat lapels
  [0,4,3,1,1,1,1,1,1,3,4,0],
  [0,4,3,1,1,1,1,1,1,3,4,0],
  [0,4,3,1,1,5,5,1,1,3,4,0], // beaker
  [0,0,4,1,1,5,5,1,1,4,0,0],
  [0,0,4,1,5,5,5,5,1,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const HARNESS_IDLE_B: SpriteFrame = HARNESS_IDLE_A;
const HARNESS_WORKING_A: SpriteFrame = HARNESS_IDLE_A;
const HARNESS_WORKING_B: SpriteFrame = HARNESS_IDLE_B;
const HARNESS_WORKING_C: SpriteFrame = HARNESS_IDLE_A;
const HARNESS_WORKING_D: SpriteFrame = HARNESS_IDLE_B;
const HARNESS_DONE_A: SpriteFrame = HARNESS_IDLE_A;
const HARNESS_DONE_B: SpriteFrame = HARNESS_IDLE_B;
const HARNESS_ERROR_A: SpriteFrame = HARNESS_IDLE_A;
const HARNESS_ERROR_B: SpriteFrame = HARNESS_IDLE_B;

// ---- ARCHITECT (teal, blueprint + ruler) ----
const ARCH_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,5,5,5,5,5,5,5,5,4,0], // blueprint roll
  [0,4,1,5,4,4,4,4,5,1,4,0],
  [0,0,4,1,1,1,1,1,1,4,0,0],
  [0,0,4,1,2,2,2,2,1,4,0,0], // ruler
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const ARCH_IDLE_B: SpriteFrame = ARCH_IDLE_A;
const ARCH_WORKING_A: SpriteFrame = ARCH_IDLE_A;
const ARCH_WORKING_B: SpriteFrame = ARCH_IDLE_B;
const ARCH_WORKING_C: SpriteFrame = ARCH_IDLE_A;
const ARCH_WORKING_D: SpriteFrame = ARCH_IDLE_B;
const ARCH_DONE_A: SpriteFrame = ARCH_IDLE_A;
const ARCH_DONE_B: SpriteFrame = ARCH_IDLE_B;
const ARCH_ERROR_A: SpriteFrame = ARCH_IDLE_A;
const ARCH_ERROR_B: SpriteFrame = ARCH_IDLE_B;

// ---- UI/APP (pink, palette + brush) ----
const UI_IDLE_A: SpriteFrame = [
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,4,1,1,3,3,1,1,4,0,0],
  [0,0,4,1,3,3,3,3,1,4,0,0],
  [0,0,4,1,3,4,4,3,1,4,0,0],
  [0,0,0,4,3,3,3,3,4,0,0,0],
  [0,0,4,4,1,1,1,1,4,4,0,0],
  [0,4,1,1,1,1,1,1,1,1,4,0],
  [0,4,2,2,2,1,1,1,1,1,4,0], // palette
  [0,4,2,5,2,2,1,1,1,1,4,0],
  [0,0,4,2,2,2,1,1,5,4,0,0], // brush
  [0,0,4,1,1,1,1,5,5,4,0,0],
  [0,0,0,4,1,1,1,1,4,0,0,0],
  [0,0,0,4,4,1,1,4,4,0,0,0],
  [0,0,0,0,4,4,4,4,0,0,0,0],
  [0,0,0,0,0,0,0,0,0,0,0,0],
];

const UI_IDLE_B: SpriteFrame = UI_IDLE_A;
const UI_WORKING_A: SpriteFrame = UI_IDLE_A;
const UI_WORKING_B: SpriteFrame = UI_IDLE_B;
const UI_WORKING_C: SpriteFrame = UI_IDLE_A;
const UI_WORKING_D: SpriteFrame = UI_IDLE_B;
const UI_DONE_A: SpriteFrame = UI_IDLE_A;
const UI_DONE_B: SpriteFrame = UI_IDLE_B;
const UI_ERROR_A: SpriteFrame = UI_IDLE_A;
const UI_ERROR_B: SpriteFrame = UI_IDLE_B;

// ---------------------------------------------------------------------------
// Exported sprite map
// ---------------------------------------------------------------------------

/** Complete sprite map indexed by agent id. */
export const HAMSTER_SPRITES: SpriteMap = {
  pm: {
    idle:    [PM_IDLE_A, PM_IDLE_B],
    working: [PM_WORKING_A, PM_WORKING_B, PM_WORKING_C, PM_WORKING_D],
    done:    [PM_DONE_A, PM_DONE_B],
    error:   [PM_ERROR_A, PM_ERROR_B],
  },
  dev: {
    idle:    [DEV_IDLE_A, DEV_IDLE_B],
    working: [DEV_WORKING_A, DEV_WORKING_B, DEV_WORKING_C, DEV_WORKING_D],
    done:    [DEV_DONE_A, DEV_DONE_B],
    error:   [DEV_ERROR_A, DEV_ERROR_B],
  },
  qa: {
    idle:    [QA_IDLE_A, QA_IDLE_B],
    working: [QA_WORKING_A, QA_WORKING_B, QA_WORKING_C, QA_WORKING_D],
    done:    [QA_DONE_A, QA_DONE_B],
    error:   [QA_ERROR_A, QA_ERROR_B],
  },
  security: {
    idle:    [SEC_IDLE_A, SEC_IDLE_B],
    working: [SEC_WORKING_A, SEC_WORKING_B, SEC_WORKING_C, SEC_WORKING_D],
    done:    [SEC_DONE_A, SEC_DONE_B],
    error:   [SEC_ERROR_A, SEC_ERROR_B],
  },
  build: {
    idle:    [BUILD_IDLE_A, BUILD_IDLE_B],
    working: [BUILD_WORKING_A, BUILD_WORKING_B, BUILD_WORKING_C, BUILD_WORKING_D],
    done:    [BUILD_DONE_A, BUILD_DONE_B],
    error:   [BUILD_ERROR_A, BUILD_ERROR_B],
  },
  harness: {
    idle:    [HARNESS_IDLE_A, HARNESS_IDLE_B],
    working: [HARNESS_WORKING_A, HARNESS_WORKING_B, HARNESS_WORKING_C, HARNESS_WORKING_D],
    done:    [HARNESS_DONE_A, HARNESS_DONE_B],
    error:   [HARNESS_ERROR_A, HARNESS_ERROR_B],
  },
  architect: {
    idle:    [ARCH_IDLE_A, ARCH_IDLE_B],
    working: [ARCH_WORKING_A, ARCH_WORKING_B, ARCH_WORKING_C, ARCH_WORKING_D],
    done:    [ARCH_DONE_A, ARCH_DONE_B],
    error:   [ARCH_ERROR_A, ARCH_ERROR_B],
  },
  ui: {
    idle:    [UI_IDLE_A, UI_IDLE_B],
    working: [UI_WORKING_A, UI_WORKING_B, UI_WORKING_C, UI_WORKING_D],
    done:    [UI_DONE_A, UI_DONE_B],
    error:   [UI_ERROR_A, UI_ERROR_B],
  },
};

/**
 * Retrieve the correct frame array for a given agent and status.
 * Falls back to idle frames if the agent id is unknown.
 *
 * @param agentId - Agent identifier string.
 * @param status - Current agent status.
 * @returns Array of sprite frames for the given status.
 */
export function getFrames(agentId: string, status: string): SpriteFrame[] {
  const sprites = HAMSTER_SPRITES[agentId];
  if (!sprites) {
    return HAMSTER_SPRITES['pm'].idle;
  }
  switch (status) {
    case 'working': return sprites.working;
    case 'done':    return sprites.done;
    case 'error':   return sprites.error;
    case 'stopped': return sprites.error; // reuse error pose for stopped
    default:        return sprites.idle;
  }
}
