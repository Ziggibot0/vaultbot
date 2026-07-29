---
type: bridge
status: complete
created: 2026-07-29
summary: "Python simulation of predator-prey dynamics using the Lotka-Volterra equations. Models how populations of predators and prey oscillate over time — the same energy flow described in Energy-Processing-in-Living-Systems. Bridge between biology cluster and Python cluster."
tags: [bridge, biology, python, predator-prey, lotka-volterra, ecosystem, energy-flow, simulation, biomimetic]
biology_links:
  - "[[Energy-Processing-in-Living-Systems]]"
  - "[[Energy-Processing-and-Metabolism]]"
  - "[[Evolution-and-Population-Genetics]]"
  - "[[Qualities-of-Life]]"
  - "[[Homeostasis-and-Animal-Regulation]]"
  - "[[Adaptation-and-Natural-Selection]]"
python_links:
  - "[[Python-3.11-Playbook]]"
  - "[[What-Is-A-Bit]]"
---

# Simulating Predator-Prey Ecosystems in Python

## The Bridge

[[Energy-Processing-in-Living-Systems]] describes how energy flows through ecosystems — sunlight → producers → consumers → decomposers. [[Energy-Processing-and-Metabolism]] explains how organisms convert energy through metabolism. But energy flow isn't steady — it **oscillates**. Predator and prey populations rise and fall in cycles: more prey → more predators → fewer prey → fewer predators → more prey → repeat.

These oscillations were first modeled mathematically by Lotka and Volterra in 1925-1926. The **Lotka-Volterra equations** describe how two populations interact:

```
dx/dt = αx - βxy    (prey: grows by itself, eaten by predators)
dy/dt = δxy - γy    (predator: grows by eating prey, dies naturally)
```

Where x = prey population, y = predator population, and α, β, δ, γ are rate constants. This is the same feedback loop [[Homeostasis-and-Animal-Regulation]] describes — but at the population level, not the individual level.

| Biology | Python |
|---|---|
| Prey population (rabbits) | Variable x |
| Predator population (foxes) | Variable y |
| Prey birth rate | α (alpha) |
| Predation rate | β (beta) |
| Predator growth from eating | δ (delta) |
| Predator death rate | γ (gamma) |
| Population oscillation | The cyclic solution of the ODEs |
| Ecosystem stability | Equilibrium point where dx/dt = dy/dt = 0 |

[[Qualities-of-Life]] lists "response to environment" and "regulation" as properties of life — predator-prey dynamics show populations doing both. [[Adaptation-and-Natural-Selection]] explains that predators and prey co-evolve — the simulation shows the population-level consequence of that arms race.

## The Simulation

```python
from dataclasses import dataclass
from typing import Callable, List, Tuple

@dataclass
class Ecosystem:
    """
    Lotka-Volterra predator-prey ecosystem.
    
    The equations model how energy flows through a food chain:
    - Prey convert environmental energy (grass/sunlight) into biomass
    - Predators convert prey biomass into predator biomass
    - Both populations oscillate around an equilibrium
    
    This is the same energy flow [[Energy-Processing-in-Living-Systems]]
    describes, but with the dynamics made explicit.
    """
    prey: float = 40.0       # Initial prey population
    predator: float = 9.0     # Initial predator population
    alpha: float = 0.1        # Prey birth rate
    beta: float = 0.02        # Predation rate (prey death per encounter)
    delta: float = 0.01        # Predator growth rate per prey eaten
    gamma: float = 0.1        # Predator natural death rate
    
    # History for tracking
    prey_history: List[float] = None
    predator_history: List[float] = None
    time_history: List[float] = None
    
    def __post_init__(self):
        if self.prey_history is None:
            self.prey_history = []
        if self.predator_history is None:
            self.predator_history = []
        if self.time_history is None:
            self.time_history = []
    
    @property
    def equilibrium_prey(self) -> float:
        """Equilibrium prey population (where dx/dt = 0)."""
        return self.gamma / self.delta
    
    @property
    def equilibrium_predator(self) -> float:
        """Equilibrium predator population (where dy/dt = 0)."""
        return self.alpha / self.beta
    
    def step(self, dt: float = 0.1) -> Tuple[float, float]:
        """
        Advance one timestep using Euler integration.
        
        dx/dt = αx - βxy  (prey grow, get eaten)
        dy/dt = δxy - γy  (predators grow from eating, die naturally)
        """
        # Lotka-Volterra equations
        dx = (self.alpha * self.prey - self.beta * self.prey * self.predator) * dt
        dy = (self.delta * self.prey * self.predator - self.gamma * self.predator) * dt
        
        self.prey = max(0, self.prey + dx)
        self.predator = max(0, self.predator + dy)
        
        return self.prey, self.predator
    
    def simulate(self, steps: int = 500, dt: float = 0.1) -> None:
        """Run the full simulation, recording history."""
        self.prey_history = []
        self.predator_history = []
        self.time_history = []
        
        t = 0.0
        for _ in range(steps):
            self.prey_history.append(self.prey)
            self.predator_history.append(self.predator)
            self.time_history.append(t)
            self.step(dt)
            t += dt
    
    def print_summary(self) -> None:
        """Print ecosystem statistics."""
        if not self.prey_history:
            print("No simulation data. Run simulate() first.")
            return
        
        print(f"Ecosystem Parameters:")
        print(f"  α (prey birth rate):    {self.alpha}")
        print(f"  β (predation rate):     {self.beta}")
        print(f"  δ (predator growth):    {self.delta}")
        print(f"  γ (predator death):     {self.gamma}")
        print(f"\nEquilibrium (stable point):")
        print(f"  Prey:     {self.equilibrium_prey:.1f}")
        print(f"  Predator: {self.equilibrium_predator:.1f}")
        print(f"\nSimulation results:")
        print(f"  Steps:        {len(self.prey_history)}")
        print(f"  Prey range:   {min(self.prey_history):.1f} – {max(self.prey_history):.1f}")
        print(f"  Pred range:   {min(self.predator_history):.1f} – {max(self.predator_history):.1f}")
        
        # Check for oscillation
        prey_mid = len(self.prey_history) // 2
        late_prey = self.prey_history[prey_mid:]
        if max(late_prey) - min(late_prey) > 5:
            print(f"  Status: OSCILLATING (populations cycle)")
        else:
            print(f"  Status: STABLE (populations converged)")


def simulate_with_carrying_capacity(
    prey: float = 40.0,
    predator: float = 9.0,
    alpha: float = 0.1,
    beta: float = 0.02,
    delta: float = 0.01,
    gamma: float = 0.1,
    carrying_capacity: float = 100.0,
    steps: int = 500,
    dt: float = 0.1,
) -> None:
    """
    Extended model with carrying capacity (logistic growth for prey).
    
    Real ecosystems have limited resources — prey can't grow forever.
    The logistic term (1 - x/K) limits prey growth as they approach K.
    
    This connects to [[Evolution-and-Population-Genetics]] which discusses
    carrying capacity as a constraint on population growth.
    """
    print("=== Predator-Prey with Carrying Capacity ===\n")
    
    x, y = prey, predator
    K = carrying_capacity
    
    for step in range(steps):
        # Modified Lotka-Volterra with logistic prey growth
        dx = (alpha * x * (1 - x / K) - beta * x * y) * dt
        dy = (delta * x * y - gamma * y) * dt
        
        x = max(0, x + dx)
        y = max(0, y + dy)
        
        if step % 50 == 0:
            print(f"Step {step:3d}: prey={x:.1f}, predator={y:.1f}")
    
    print(f"\nFinal: prey={x:.1f}, predator={y:.1f}")
    print(f"Carrying capacity: {K:.1f}")
    print(f"\nWith carrying capacity, oscillations may dampen —")
    print(f"the system reaches a stable equilibrium instead of cycling forever.")


if __name__ == "__main__":
    print("=== Simulating Predator-Prey Dynamics (Lotka-Volterra) ===\n")
    
    eco = Ecosystem(prey=40, predator=9, alpha=0.1, beta=0.02, delta=0.01, gamma=0.1)
    eco.simulate(steps=500, dt=0.1)
    eco.print_summary()
    
    print("\n  The cycle:")
    print("  1. Prey abundant → predators feast and reproduce")
    print("  2. More predators → prey gets overhunted, population drops")
    print("  3. Less prey → predators starve, population drops")
    print("  4. Less predators → prey recovers, population rises")
    print("  5. Back to step 1 — the cycle of life")
    
    print()
    simulate_with_carrying_capacity()
```

## How This Connects

**Biology side:** [[Energy-Processing-in-Living-Systems]] describes energy flow through ecosystems — the Lotka-Volterra equations model that flow quantitatively. [[Energy-Processing-and-Metabolism]] explains how organisms convert energy — the `alpha` parameter represents prey converting environmental energy into biomass. [[Evolution-and-Population-Genetics]] discusses population dynamics and carrying capacity — the extended model implements that constraint. [[Qualities-of-Life]] lists regulation as a property of life — predator-prey cycles are population-level regulation. [[Homeostasis-and-Animal-Regulation]] describes feedback loops — the Lotka-Volterra system IS a feedback loop (predator population is regulated by prey availability, prey population is regulated by predator pressure). [[Adaptation-and-Natural-Selection]] explains co-evolution — predator and prey evolve in response to each other, which is why the parameters (α, β, δ, γ) change over evolutionary time.

**Python side:** Uses dataclasses with computed properties (`equilibrium_prey`, `equilibrium_predator`), list history tracking, and Euler integration — all patterns from [[Python-3.11-Playbook]]. The `@dataclass` with `__post_init__` demonstrates initialization patterns. The separation of `step()` and `simulate()` follows good API design. [[What-Is-A-Bit]] shows how continuous quantities (populations) are discretized — here, continuous differential equations become discrete timesteps.

**Biomimetic side:** The Lotka-Volterra model is one of the oldest examples of mathematical biology — it shows that biological systems can be described with the same rigor as physical systems. The oscillation pattern (boom-bust cycles) appears in economics, technology adoption, and even VaultBot's own research cycles (intense research → saturation → new topic → repeat). The carrying capacity model connects to [[Fractal-Entropy-Principle]] — systems can't grow forever; entropy eventually constrains growth.

## Python Textbook References

This simulation uses:
- [[python-9classes]] — dataclasses with computed properties
- [[python-4more-control-flow-tools]] — for loops, range()

## VaultBot Architecture Connection

This simulation maps to the dynamics of VaultBot's research cycles:

- [[Biomimetic-Engineering-for-Self-Improving-AI]] identifies population dynamics as a model for resource management in self-improving systems.
- The Lotka-Volterra oscillation maps to VaultBot's research cycles: intense research → knowledge saturation → new topic discovery → repeat. The "prey" is unexplored knowledge gaps; the "predator" is the autonomous researcher consuming them.
- [[Fractal-Entropy-Principle]] explains why oscillation happens: systems can't grow forever. The carrying capacity model (logistic growth) is the same constraint the vault hits when a topic is exhausted.
- The equilibrium point (where dx/dt = dy/dt = 0) maps to [[How-to-Decide-When-to-Research-vs-Answer]] — knowing when the system has enough knowledge to answer without more research.
- [[Energy-Processing-in-Living-Systems]] explains that energy flows through ecosystems in cycles. VaultBot's energy (LLM calls, research budget) flows the same way — concentrated in hot topics, dispersed when topics cool.

**The deep connection:** VaultBot's research dynamics ARE predator-prey dynamics. Knowledge gaps are prey; the researcher is the predator. When gaps are abundant, research is productive (predators thrive). As gaps are consumed, research slows (predators starve). Then new gaps emerge (new prey), and the cycle restarts. The vault's research roadmap is an ecosystem, and this simulation shows its math.

## Related Bridge Notes

- [[Simulating-Natural-Selection-in-Python]] — the genetic algorithm is the individual-level view of what the Lotka-Volterra model shows at the population level. Selection determines which organisms survive; predator-prey dynamics determine how populations of selected organisms oscillate.
