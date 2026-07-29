---
type: bridge
status: complete
created: 2026-07-29
summary: "Python simulation of slime mold (Physarum polycephalum) network optimization. Slime molds find shortest paths through chemical gradients without a brain — this simulation replicates that ability using a gradient-following algorithm. Bridge between biology cluster and Python cluster."
tags: [bridge, biology, python, slime-mold, pathfinding, gradient, network-optimization, simulation, biomimetic]
biology_links:
  - "[[Slime-Molds-Intelligence-Without-Brain]]"
  - "[[Cell-Membranes-Structure-Strength-Exploitation]]"
  - "[[Basic-Chemistry-Solubility-Gradients-Hydrophobic]]"
  - "[[Qualities-of-Life]]"
python_links:
  - "[[Python-3.11-Playbook]]"
  - "[[What-Is-A-Bit]]"
---

# Simulating Slime Mold Pathfinding in Python

## The Bridge

[[Slime-Molds-Intelligence-Without-Brain]] describes how Physarum polycephalum — a brainless slime mold — solves mazes and optimizes networks. The famous Tokyo experiment showed that slime mold placed on oat flakes representing cities grew a network that closely matched the Tokyo rail system. The mechanism is **chemotaxis**: the mold follows chemical gradients, reinforcing paths where food is detected and abandoning paths where it isn't.

This simulation replicates that process. A grid of "slime mold cells" follows a food gradient, reinforcing successful paths and pruning dead ends — the same algorithm nature discovered over billions of years.

| Biology | Python |
|---|---|
| Slime mold plasmodium | Grid of cells with concentration values |
| Chemical gradient (chemotaxis) | Food source diffuses across grid |
| Path reinforcement | Cells with high flow get stronger |
| Path abandonment | Cells with low flow decay |
| Food source (oat flakes) | Target nodes in the graph |
| Network optimization | Shortest path emerges from local rules |

[[Basic-Chemistry-Solubility-Gradients-Hydrophobic]] explains how gradients drive movement in biological systems. [[Cell-Membranes-Structure-Strength-Exploitation]] describes how membranes channel that movement. The slime mold uses both — it follows gradients through its membrane-bound tubes.

## The Simulation

```python
import random
from typing import List, Tuple, Set
from dataclasses import dataclass

@dataclass
class SlimeGrid:
    """
    Grid simulating Physarum polycephalum network optimization.
    
    Each cell has:
    - food: concentration of food chemical (diffuses from sources)
    - mold: amount of slime mold present (grows toward food)
    - flow: how much material is flowing through this cell (reinforces paths)
    """
    width: int
    height: int
    food: List[List[float]] = None
    mold: List[List[float]] = None
    flow: List[List[float]] = None
    
    def __post_init__(self):
        if self.food is None:
            self.food = [[0.0] * self.width for _ in range(self.height)]
        if self.mold is None:
            self.mold = [[0.0] * self.width for _ in range(self.height)]
        if self.flow is None:
            self.flow = [[0.0] * self.width for _ in range(self.height)]


def simulate_slime_mold(
    width: int = 30,
    height: int = 20,
    food_sources: List[Tuple[int, int]] = None,
    start: Tuple[int, int] = (2, 10),
    steps: int = 100,
    diffusion_rate: float = 0.1,
    decay_rate: float = 0.05,
    growth_rate: float = 0.3,
) -> SlimeGrid:
    """
    Simulate slime mold finding paths between food sources.
    
    The algorithm:
    1. Food sources emit a chemical that diffuses across the grid
    2. Mold grows toward higher food concentration (chemotaxis)
    3. Flow is reinforced where mold successfully reaches food
    4. Mold decays where flow is low (path abandonment)
    5. Over time, only the most efficient paths survive
    
    This is how [[Slime-Molds-Intelligence-Without-Brain]] describes
    the Tokyo rail network experiment — emergent optimization from local rules.
    """
    if food_sources is None:
        food_sources = [(25, 10), (15, 5), (15, 15), (20, 17)]
    
    grid = SlimeGrid(width=width, height=height)
    
    # Place food sources
    for fx, fy in food_sources:
        grid.food[fy][fx] = 100.0
    
    # Place initial mold at start
    grid.mold[start[1]][start[0]] = 50.0
    
    for step in range(steps):
        # 1. Diffuse food chemical (gradient formation)
        new_food = [row[:] for row in grid.food]
        for y in range(height):
            for x in range(width):
                for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        exchange = (grid.food[ny][nx] - grid.food[y][x]) * diffusion_rate
                        new_food[y][x] += exchange
        grid.food = new_food
        
        # 2. Mold grows toward food gradient (chemotaxis)
        new_mold = [row[:] for row in grid.mold]
        for y in range(height):
            for x in range(width):
                if grid.mold[y][x] < 0.1:
                    continue
                # Find the neighbor with highest food concentration
                best_ny, best_nx = y, x
                best_food = grid.food[y][x]
                for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1, -1), (-1, 1), (1, -1), (1, 1)]:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        if grid.food[ny][nx] > best_food:
                            best_food = grid.food[ny][nx]
                            best_ny, best_nx = ny, nx
                
                # Grow toward the best neighbor
                if (best_ny, best_nx) != (y, x):
                    growth = grid.mold[y][x] * growth_rate
                    new_mold[best_ny][best_nx] += growth
                    # Record flow (reinforces this path)
                    grid.flow[best_ny][best_nx] += growth
        
        # 3. Decay: mold without flow dies back (path abandonment)
        for y in range(height):
            for x in range(width):
                grid.flow[y][x] *= (1 - decay_rate)
                if grid.flow[y][x] < 0.1:
                    new_mold[y][x] *= (1 - decay_rate)
        
        grid.mold = new_mold
        
        if step % 20 == 0:
            active = sum(1 for y in range(height) for x in range(width) 
                         if grid.mold[y][x] > 1.0)
            total_flow = sum(sum(row) for row in grid.flow)
            print(f"Step {step:3d}: active_cells={active}, total_flow={total_flow:.1f}")
    
    return grid


def visualize_paths(grid: SlimeGrid, threshold: float = 1.0) -> str:
    """ASCII visualization of the slime mold's network."""
    lines = []
    for y in range(grid.height):
        row = ""
        for x in range(grid.width):
            if grid.food[y][x] > 50:
                row += "F"  # Food source
            elif grid.mold[y][x] > threshold * 3:
                row += "#"  # Strong path
            elif grid.mold[y][x] > threshold:
                row += "."  # Weak path
            elif grid.flow[y][x] > threshold:
                row += "~"  # Flow without mold
            else:
                row += " "  # Empty
        lines.append(row)
    return "\n".join(lines)


if __name__ == "__main__":
    print("=== Simulating Slime Mold Pathfinding ===\n")
    print("Mold starts at (2,10), food at: (25,10), (15,5), (15,15), (20,17)\n")
    
    grid = simulate_slime_mold(steps=80)
    
    print("\n=== Network Visualization ===")
    print("(F=food, #=strong path, .=weak path, ~=flow, space=empty)\n")
    print(visualize_paths(grid))
```

## How This Connects

**Biology side:** [[Slime-Molds-Intelligence-Without-Brain]] describes how Physarum polycephalum solves mazes and builds efficient networks — this simulation replicates both. [[Basic-Chemistry-Solubility-Gradients-Hydrophobic]] explains the gradient-driven movement — the `diffusion_rate` parameter models that gradient. [[Cell-Membranes-Structure-Strength-Exploitation]] describes how membranes channel flow — the mold's tubes are membrane-bound channels. [[Qualities-of-Life]] lists "response to stimuli" as a property of life — the mold's chemotaxis is exactly that.

**Python side:** Uses dataclasses, type hints, list comprehensions, and nested list manipulation from [[Python-3.11-Playbook]]. The grid-based approach demonstrates 2D array processing. The ASCII visualization is a classic Python pattern for quick debugging. [[What-Is-A-Bit]] shows how information reduces to bits — here, continuous biological processes become discrete grid values.

**Biomimetic side:** This is one of the most direct biomimetic bridges in the vault. The slime mold's algorithm — local rules producing global optimization — is exactly what [[Biomimetic-Engineering-for-Self-Improving-AI]] advocates. The same principle (stigmergy: indirect coordination through environmental modification) could be applied to VaultBot's autonomous researcher: it leaves "chemical trails" (notes) that guide future research.

## Python Textbook References

This simulation uses:
- [[python-9classes]] — dataclasses
- [[python-5data-structures]] — 2D grid as nested lists

## VaultBot Architecture Connection

This simulation maps to how VaultBot navigates its own knowledge:

- [[Biomimetic-Engineering-for-Self-Improving-AI]] identifies slime mold intelligence as a model for decentralized, gradient-following optimization.
- The slime mold's chemotaxis maps to [[vault_search]] — VaultBot follows semantic gradients (FAISS embeddings) to find relevant knowledge, the same way the mold follows chemical gradients to find food.
- The path reinforcement/abandonment mechanism maps to the [[vault_cluster_analyzer]] — paths that get used more become stronger (more wikilinks), paths that get ignored decay.
- [[Slime-Molds-Intelligence-Without-Brain]] shows that intelligence doesn't require a central controller. VaultBot's autonomous researcher works the same way — it follows "chemical trails" (dangling wikilinks, thin notes) and grows toward them.
- The Tokyo rail network experiment connects to [[Cross-Session-Patterns-from-75-Chat-Logs]] — both show how simple local rules produce globally optimized structures.

**The deep connection:** VaultBot navigates its knowledge the way a slime mold navigates a maze. It follows gradients (semantic similarity), reinforces successful paths (wikilinks between connected ideas), and abandons dead ends (prunes broken links). The vault's graph IS a slime mold network — it self-organizes through local rules into globally efficient structure.

## Related Bridge Notes

- [[Simulating-Phylogenetic-Trees-in-Python]] — both are about biological information processing without a central controller. Slime molds optimize networks through local gradient-following; phylogenetic trees organize relationships through distance-based clustering. Both show how structure emerges from simple rules — the same principle behind the vault's wikilink graph.
