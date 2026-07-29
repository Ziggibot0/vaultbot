---
type: bridge
status: complete
created: 2026-07-29
summary: "Python simulation of diffusion across a cell membrane using a cellular automaton. Particles move from high concentration to low concentration through a semipermeable barrier — the same process described in Cell-Membranes-Structure-Strength-Exploitation. Bridge between biology cluster and Python cluster."
tags: [bridge, biology, python, diffusion, membrane, cellular-automaton, simulation, biomimetic]
biology_links:
  - "[[Cell-Membranes-Structure-Strength-Exploitation]]"
  - "[[Protocell-Theory-Origin-of-Life]]"
  - "[[Cell-Structure-Organelles]]"
  - "[[Basic-Chemistry-Solubility-Gradients-Hydrophobic]]"
  - "[[Slime-Molds-Intelligence-Without-Brain]]"
python_links:
  - "[[Python-3.11-Playbook]]"
  - "[[What-Is-A-Bit]]"
---

# Simulating Cell Membrane Diffusion in Python

## The Bridge

[[Cell-Membranes-Structure-Strength-Exploitation]] describes the cell membrane as a selectively permeable barrier — the fluid mosaic model where phospholipids form a bilayer that lets some molecules pass and blocks others. [[Basic-Chemistry-Solubility-Gradients-Hydrophobic]] explains the gradient: molecules move from high concentration to low concentration (diffusion) or from low water concentration to high water concentration (osmosis). [[Protocell-Theory-Origin-of-Life]] explains that the first step toward life was forming a membrane-bounded compartment.

This note simulates diffusion across a membrane using a **cellular automaton** — a grid where each cell holds a concentration value, and particles flow according to simple rules. The membrane is a row of cells with lower permeability.

| Biology | Python |
|---|---|
| Cell membrane (lipid bilayer) | Grid row with reduced diffusion rate |
| Concentration gradient | Different values across grid cells |
| Diffusion (passive transport) | Each cell averages with neighbors |
| Selective permeability | Membrane row has lower exchange rate |
| Equilibrium | All cells reach similar values |
| Osmosis (water follows solute) | Special case where one substance is blocked |

[[Slime-Molds-Intelligence-Without-Brain]] shows that even simple organisms use chemical gradients for navigation — the same gradient this simulation produces. [[What-Is-A-Bit]] shows how all digital information reduces to bits; here, continuous concentration values are discretized into a grid.

## The Simulation

```python
import random
from typing import List

def simulate_diffusion(
    grid_size: int = 20,
    membrane_row: int = 10,
    membrane_permeability: float = 0.1,
    initial_left_concentration: float = 100.0,
    initial_right_concentration: float = 0.0,
    steps: int = 200,
) -> List[List[float]]:
    """
    Simulate diffusion across a semipermeable membrane.
    
    The grid is a 2D array of concentration values.
    - Left half starts with high concentration (inside the cell)
    - Right half starts with low concentration (outside)
    - The membrane row has reduced permeability
    - Each step, each cell exchanges concentration with neighbors
    
    This is how [[Cell-Membranes-Structure-Strength-Exploitation]] describes
    passive transport: molecules flow down their concentration gradient.
    """
    # Initialize grid: left side has high concentration, right side low
    grid = [[0.0] * grid_size for _ in range(grid_size)]
    for r in range(grid_size):
        for c in range(grid_size):
            if c < membrane_row:
                grid[r][c] = initial_left_concentration
            else:
                grid[r][c] = initial_right_concentration
    
    history = []
    
    for step in range(steps):
        new_grid = [row[:] for row in grid]  # Copy
        
        for r in range(grid_size):
            for c in range(grid_size):
                # Diffuse with neighbors (up, down, left, right)
                neighbors = []
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < grid_size and 0 <= nc < grid_size:
                        # Check if crossing the membrane
                        is_membrane_crossing = (
                            (c == membrane_row - 1 and nc == membrane_row) or
                            (c == membrane_row and nc == membrane_row - 1)
                        )
                        rate = membrane_permeability if is_membrane_crossing else 0.25
                        exchange = (grid[nr][nc] - grid[r][c]) * rate
                        new_grid[r][c] += exchange
        
        grid = new_grid
        history.append([row[:] for row in grid])
        
        if step % 50 == 0 or step == steps - 1:
            avg_left = sum(grid[r][c] for r in range(grid_size) for c in range(membrane_row)) / (grid_size * membrane_row)
            avg_right = sum(grid[r][c] for r in range(grid_size) for c in range(membrane_row, grid_size)) / (grid_size * (grid_size - membrane_row))
            ratio = avg_left / avg_right if avg_right > 0.01 else float('inf')
            print(f"Step {step:3d}: left={avg_left:.2f}, right={avg_right:.2f}, ratio={ratio:.2f}")
    
    return history


def simulate_osmosis(
    container_size: int = 30,
    membrane_pos: int = 15,
    solute_left: float = 80.0,
    solute_right: float = 10.0,
    water_permeability: float = 0.15,
    solute_permeability: float = 0.01,  # Membrane blocks solute
    steps: int = 100,
) -> List[dict]:
    """
    Simulate osmosis: water crosses the membrane to equalize solute concentration,
    but the solute (which the membrane blocks) stays mostly put.
    
    This is the process [[Protocell-Theory-Origin-of-Life]] describes —
    early protocells with lipid membranes creating chemical gradients.
    """
    solute = [solute_left if i < membrane_pos else solute_right 
              for i in range(container_size)]
    water = [100.0 - s for s in solute]  # Water = total - solute
    
    history = []
    
    for step in range(steps):
        new_solute = solute[:]
        new_water = water[:]
        
        for i in range(container_size - 1):
            # Water diffuses freely (high permeability)
            w_diff = (water[i + 1] - water[i]) * water_permeability
            new_water[i] += w_diff
            new_water[i + 1] -= w_diff
            
            # Solute diffuses slowly (low permeability = selective membrane)
            is_membrane = (i == membrane_pos - 1)
            s_rate = solute_permeability if is_membrane else 0.2
            s_diff = (solute[i + 1] - solute[i]) * s_rate
            new_solute[i] += s_diff
            new_solute[i + 1] -= s_diff
        
        solute = new_solute
        water = new_water
        
        if step % 20 == 0:
            left_total = sum(solute[:membrane_pos]) + sum(water[:membrane_pos])
            right_total = sum(solute[membrane_pos:]) + sum(water[membrane_pos:])
            print(f"Step {step:3d}: left_solute={sum(solute[:membrane_pos]):.1f}, "
                  f"right_solute={sum(solute[membrane_pos:]):.1f}, "
                  f"left_water={sum(water[:membrane_pos]):.1f}, "
                  f"right_water={sum(water[membrane_pos:]):.1f}")
        
        history.append({'solute': solute[:], 'water': water[:]})
    
    return history


if __name__ == "__main__":
    print("=== Simulating Diffusion Across a Cell Membrane ===\n")
    simulate_diffusion()
    
    print("\n=== Simulating Osmosis (selective membrane) ===\n")
    simulate_osmosis()
```

## How This Connects

**Biology side:** [[Cell-Membranes-Structure-Strength-Exploitation]] describes the fluid mosaic model and selective permeability — the `membrane_permeability` parameter models that selectivity. [[Protocell-Theory-Origin-of-Life]] explains that life began with membrane-bounded compartments — the simulation shows how membranes create and maintain gradients. [[Basic-Chemistry-Solubility-Gradients-Hydrophobic]] explains the chemical basis of gradients — the simulation implements those gradients numerically. [[Cell-Structure-Organelles]] describes how organelles use membrane-bound compartments — each could be simulated as a region with its own permeability.

**Python side:** Uses nested lists (2D arrays), list comprehensions, and tuple unpacking from [[Python-3.11-Playbook]]. The grid-based approach demonstrates how continuous physical processes are discretized in code. [[What-Is-A-Bit]] shows how all information reduces to bits — here, continuous concentrations become discrete grid values.

**Biomimetic side:** Cellular automata are one of the oldest biomimetic computing models — they were invented by John von Neumann in the 1940s, inspired by self-reproduction in biology. The same grid-and-rules approach connects to [[Slime-Molds-Intelligence-Without-Brain]], where simple local rules produce emergent intelligent behavior.

## Python Textbook References

This simulation uses:
- [[python-5data-structures]] — nested lists as 2D arrays
- [[python-4more-control-flow-tools]] — nested for loops, range()

## VaultBot Architecture Connection

This simulation maps to how knowledge flows through the vault:

- [[Biomimetic-Engineering-for-Self-Improving-AI]] identifies membranes and selective permeability as a mechanism for controlling information flow.
- The cell membrane's selective permeability maps to [[Vault-Knowledge-Only-Directive]] — the vault is selectively permeable. It lets in sourced knowledge but blocks training-data hallucinations.
- [[Protocell-Theory-Origin-of-Life]] explains that life began with a membrane-bounded compartment. The vault's boundary (what's in it vs what's not) is its membrane. [[VaultBot-Is-the-Vault]] makes this explicit.
- The diffusion simulation models how [[context_budgeter.py]] works — knowledge diffuses from high-density areas (rich notes) to low-density areas (thin notes) through the retrieval system.
- [[Fractal-Entropy-Principle]]: the membrane resists entropy by maintaining gradients. The vault resists knowledge entropy by maintaining quality gradients (exemplars vs raw research).

**The deep connection:** The vault IS a membrane-bounded system. Its wikilinks are the membrane channels. Context retrieval is diffusion — knowledge flows from where it's concentrated (well-linked notes) to where it's needed (the current query). The vault's organization determines what knowledge can flow where.

## Related Bridge Notes

- [[Simulating-Homeostasis-in-Python]] — diffusion is the passive transport that homeostasis regulates. The membrane controls what diffuses; the homeostat controls how much. Together they model the two ways biological systems maintain internal stability: passive equilibrium and active regulation.
