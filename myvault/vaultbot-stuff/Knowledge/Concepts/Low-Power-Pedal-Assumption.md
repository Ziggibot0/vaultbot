---
type: concept
status: active
baseline: true
created: 2026-08-06
tags:
  - electronics
  - guitar-pedals
  - power-consumption
  - pt2399
  - assumptions
summary: "The Low-Power Pedal Assumption — the common belief that guitar delay pedals draw under 5mA, which the PT2399 chip contradicts by drawing ~25mA at idle."
---

# Low-Power Pedal Assumption

The **Low-Power Pedal Assumption** is the common belief that guitar delay/effects pedals draw very little current — typically under 5mA — making them safe for battery-powered designs without special power management.

## The Reality

The PT2399 delay chip, used in many popular delay pedals, draws approximately **25mA at idle** — five times the assumed maximum. This means:

- **Battery drain** is a real concern in PT2399-based designs
- A **voltage regulator** is needed in battery-powered builds
- The assumption that "delay pedals are low-power" is invalid for PT2399 designs

## Why It Matters

The assumption leads to design mistakes: builders who assume <5mA draw may skip voltage regulation or use undersized batteries, resulting in pedals that die quickly or behave erratically. Knowing the actual draw forces better power design.

## Context

This concept emerged in a discussion about the [[Ephemeral-Argument-Architecture]] — it was used as an example of how typed edges (`contradicts::[[Low-Power Pedal Assumption]]`) could encode relationships between notes. The discussion concluded that writing the reasoning directly into prose (as this note does) is lower-maintenance than maintaining typed edge systems.

## Related

- [[Ephemeral-Argument-Architecture]] — the typed-edge system this concept was used to test
- [[Battery Drain in PT2399 Pedals]] — the practical consequence of the assumption being wrong
