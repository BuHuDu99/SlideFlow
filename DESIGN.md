---
version: alpha
name: SlideFlow
description: Warm, dark macOS utility that turns YouTube lectures into slide PDFs and transcripts.
colors:
  background: "#191614"
  input: "#151210"
  topbar: "#211D1A"
  panel: "#2A231F"
  card: "#24201D"
  surface: "#322B27"
  surface-hover: "#433731"
  surface-pressed: "#5A4941"
  border: "#52443D"
  track: "#493A33"
  scrollbar: "#765040"
  scrollbar-hover: "#98604B"
  text: "#FFFFFF"
  muted: "#C6B9B2"
  faint: "#9A8E88"
  placeholder: "#968982"
  primary: "#D24726"
  primary-hover: "#B83A20"
  on-primary: "#FFFFFF"
  accent-soft: "#4A251B"
  accent-text: "#FFB59E"
  success-soft: "#1F3A2A"
  success-text: "#8FE0AE"
  danger-soft: "#4A1F1F"
  danger-text: "#FFA8A0"
typography:
  brand:
    fontFamily: SF Pro (system)
    fontSize: 19px
    fontWeight: 700
  title:
    fontFamily: SF Pro (system)
    fontSize: 18px
    fontWeight: 700
  body-strong:
    fontFamily: SF Pro (system)
    fontSize: 13px
    fontWeight: 700
  body:
    fontFamily: SF Pro (system)
    fontSize: 13px
    fontWeight: 400
  caption:
    fontFamily: SF Pro (system)
    fontSize: 11px
    fontWeight: 400
  overline:
    fontFamily: SF Pro (system)
    fontSize: 10px
    fontWeight: 700
rounded:
  sm: 6px
  md: 8px
  lg: 9px
  xl: 12px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 22px
  xl: 28px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.lg}"
    height: 44px
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    height: 44px
  button-secondary-hover:
    backgroundColor: "{colors.surface-hover}"
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
  button-ghost-hover:
    backgroundColor: "{colors.surface}"
  card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.xl}"
  status-badge:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.xl}"
    size: 54px
  file-tag:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.overline}"
    rounded: "{rounded.sm}"
---

# SlideFlow design system

The token values above are mirrored as class constants on `SlideFlowApp` in
`main.py`. Change both together.

## Overview

SlideFlow is a quiet desktop utility: paste a link, press one button, then wait.
The interface should feel calm while long jobs run. It uses a warm, dark palette
with one orange accent, and the accent is reserved for the action or state that
matters right now.

## Colors

- **One filled accent per screen.** Only `primary` fills the main action
  ("Tạo slide", "Mở PDF", "Bắt đầu"). Every other control is secondary
  (`surface`) or ghost (transparent with a `border` outline).
- **Soft and text pairs carry state.** The status badge uses the
  `accent-*`, `success-*`, or `danger-*` pair. Stopped and paused states use
  `surface` with `muted`. Never use the accent pair for reference content such
  as history rows.
- **Text tiers:** `text` for titles and values, `muted` for descriptions,
  `faint` for the footer and shortcut hints, and `placeholder` only inside
  inputs.
- **Contrast:** every text and background pair meets WCAG AA (4.5:1 or
  higher). White on `primary` is exactly 4.5:1, so primary buttons use bold
  text of at least 13px.

## Typography

Use the system font (SF Pro) through `ctk.CTkFont`. Size and weight carry the
hierarchy, so do not add new colors to emphasize text. Uppercase `overline`
labels are only for setting names in the settings bar.

## Layout

Use 22px for the window gutter and 28px inside cards. Rows are 7px apart.
Between cards, use 12px.

## Elevation & Depth

The interface has no shadows. Depth comes from steps in the background color:
`input`, then `background`, then `topbar`, then `card`, then `surface`, plus a
1px `border` on interactive and container edges.

## Shapes

Use 12px corners for cards and the status badge, 8–9px for buttons and
inputs, and 6px for small tags.

## Components

- **Toolbar:** paste (secondary), URL field, transcript actions (ghost), and
  "Tạo slide" (primary), in that order.
- **Status card:** the badge glyph and color follow `STATUS_TONES`:
  `▶` idle, `●` running, `✓` success, `!` error, `■` stopped. Set the tone
  through `set_tone()` and the progress through `set_progress()`. Do not call
  the widgets directly.
- **History rows:** a neutral `PDF` file tag, the title, a muted timestamp,
  and a secondary "Mở PDF" button.
- **Queue rows:** a numbered badge. Only the running item uses the accent
  pair.

### Motion

- Animate only to explain a change in state. The only animation today is the
  progress bar, which eases forward over 240ms with an ease-out cubic.
- Resets and decreases jump immediately. Enter and change animations use
  ease-out and never ease-in.
- Respect macOS **Reduce motion**: when it is on, values change instantly.
- Do not animate hover, keyboard shortcuts, or anything that runs many times a
  minute.

## Do's and Don'ts

- Do keep one primary button visible per context.
- Do write status text as a short title plus one muted detail line.
- Don't add new hex literals in `main.py`. Add a token here and on the class.
- Don't use emoji. Single typographic glyphs (`✓ ▶ ● ■ ↺ ≡`) are the icon set.
- Don't decorate lists with accent number badges unless the order is the
  information.
