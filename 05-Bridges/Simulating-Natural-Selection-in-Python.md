---
type: bridge
status: complete
created: 2026-07-29
summary: "Python simulation of biological natural selection using a genetic algorithm. A population of candidate solutions evolves through mutation, crossover, and fitness-based selection — the same mechanism described in Evolution-and-Population-Genetics. Bridge between biology cluster and Python cluster."
tags: [bridge, biology, python, genetic-algorithm, natural-selection, simulation, biomimetic]
biology_links:
  - "[[Evolution-and-Population-Genetics]]"
  - "[[Adaptation-and-Natural-Selection]]"
  - "[[Darwinian-Coding-Mutation-Testing-Evolution]]"
  - "[[Reproduction-and-Genetic-Inheritance]]"
  - "[[Qualities-of-Life]]"
python_links:
  - "[[Python-3.11-Playbook]]"
  - "[[What-Is-A-Bit]]"
  - "[[Exemplar-Tool-Creation]]"
---

# Simulating Natural Selection in Python

## The Bridge

[[Evolution-and-Population-Genetics]] describes how populations evolve through four mechanisms: natural selection, mutation, gene flow, and genetic drift. [[Adaptation-and-Natural-Selection]] explains how traits that improve survival and reproduction become more common over generations. This note shows how to simulate that exact process in Python using a **genetic algorithm** — a program that evolves solutions to problems the same way nature evolves organisms.

The mapping is literal, not metaphorical:

| Biology | Python |
|---|---|
| Organism | Candidate solution (a string/list of values) |
| Genome | Encoded solution (the data structure) |
| Fitness | Score function (how well the solution works) |
| Mutation | Random changes to the genome |
| Crossover | Recombining two parents' genomes |
| Selection | Keeping high-fitness individuals |
| Generation | One iteration of the loop |
| Population | All current candidate solutions |

This is the same connection [[Darwinian-Coding-Mutation-Testing-Evolution]] makes — code is alive, in the sense that it exhibits mutational robustness and neutral networks. [[Qualities-of-Life]] lists reproduction and response to environment as defining properties of life; a genetic algorithm does both.

## The Simulation

```python
import random
from dataclasses import dataclass, field
from typing import Callable, List

@dataclass
class Organism:
    """A single candidate solution — the 'genome' is a list of floats."""
    genome: List[float]
    fitness: float = 0.0

def simulate_natural_selection(
    fitness_fn: Callable[[List[float]], float],
    genome_length: int = 10,
    population_size: int = 100,
    generations: int = 50,
    mutation_rate: float = 0.1,
    crossover_rate: float = 0.7,
    elitism: int = 2,
) -> List[Organism]:
    """
    Evolve a population to maximize fitness_fn.
    
    Mirrors biological evolution:
    - Random initialization = first generation (like random mutations in nature)
    - Fitness evaluation = environmental pressure (natural selection)
    - Selection = survival of the fittest
    - Crossover = sexual reproduction / genetic recombination
    - Mutation = random genetic drift
    - Elitism = heredity (best traits preserved across generations)
    """
    # Initialize population — random genomes, like the first organisms
    population = [
        Organism(genome=[random.gauss(0, 1) for _ in range(genome_length)])
        for _ in range(population_size)
    ]
    
    for gen in range(generations):
        # Evaluate fitness — the environment tests each organism
        for org in population:
            org.fitness = fitness_fn(org.genome)
        
        # Sort by fitness — natural selection ranks organisms
        population.sort(key=lambda o: o.fitness, reverse=True)
        
        best = population[0]
        print(f"Gen {gen}: best fitness = {best.fitness:.4f}")
        
        # Elitism: the top organisms survive unchanged (heredity)
        next_gen = population[:elitism]
        
        # Fill the rest with offspring
        while len(next_gen) < population_size:
            # Tournament selection — organisms compete for mating
            parent1 = tournament_select(population)
            parent2 = tournament_select(population)
            
            # Crossover — genetic recombination during reproduction
            if random.random() < crossover_rate:
                child_genome = crossover(parent1.genome, parent2.genome)
            else:
                child_genome = parent1.genome[:]
            
            # Mutation — random genetic changes
            child_genome = mutate(child_genome, mutation_rate)
            
            next_gen.append(Organism(genome=child_genome))
        
        population = next_gen
    
    # Final evaluation
    for org in population:
        org.fitness = fitness_fn(org.genome)
    population.sort(key=lambda o: o.fitness, reverse=True)
    return population


def tournament_select(population: List[Organism], k: int = 3) -> Organism:
    """Tournament selection: pick k random organisms, return the fittest."""
    contestants = random.sample(population, min(k, len(population)))
    return max(contestants, key=lambda o: o.fitness)


def crossover(parent_a: List[float], parent_b: List[float]) -> List[float]:
    """Single-point crossover: recombine two parents' genomes."""
    point = random.randint(1, len(parent_a) - 1)
    return parent_a[:point] + parent_b[point:]


def mutate(genome: List[float], rate: float) -> List[float]:
    """Random mutation: each gene has a chance to change."""
    return [
        gene + random.gauss(0, 0.5) if random.random() < rate else gene
        for gene in genome
    ]


# Example: evolve a genome that maximizes the sum of squares
# (a simple target — in practice, fitness_fn could evaluate anything)
if __name__ == "__main__":
    def fitness(genome):
        return sum(x ** 2 for x in genome)
    
    final_pop = simulate_natural_selection(
        fitness_fn=fitness,
        genome_length=10,
        population_size=50,
        generations=30,
    )
    
    best = final_pop[0]
    print(f"\nBest genome: {[round(g, 3) for g in best.genome]}")
    print(f"Best fitness: {best.fitness:.4f}")
```

## How This Connects

**Biology side:** [[Evolution-and-Population-Genetics]] explains the four mechanisms of evolution — this simulation implements all four. [[Adaptation-and-Natural-Selection]] describes how fitness determines survival — the `fitness_fn` is the environment. [[Reproduction-and-Genetic-Inheritance]] explains how traits pass to offspring — `crossover()` is genetic recombination. [[Darwinian-Coding-Mutation-Testing-Evolution]] already connected code to evolution; this note makes the connection executable.

**Python side:** Uses dataclasses ([[Python-3.11-Playbook]] § Classes), type hints, list comprehensions, and the `random` module — all standard Python 3.11. The `@dataclass` decorator and `Callable` type hint are patterns from the playbook. [[What-Is-A-Bit]] shows how bits encode all information; here, a genome is a list of floats encoded as bits in memory. [[Exemplar-Tool-Creation]] demonstrates tool design patterns used here (docstrings, type hints, separation of concerns).

**Biomimetic side:** This is exactly what [[Biomimetic-Engineering-for-Self-Improving-AI]] advocates — taking a biological mechanism (natural selection) and engineering it into a working system. The genetic algorithm is one of the oldest and most successful biomimetic algorithms in computing.

## Python Textbook References

This simulation uses:
- [[python-9classes]] — dataclasses (@dataclass, field)
- [[python-5data-structures]] — lists, list comprehensions
- [[python-4more-control-flow-tools]] — for loops, if/else, random module

## VaultBot Architecture Connection

This simulation isn't just an exercise — it maps directly to how VaultBot improves:

- [[Biomimetic-Engineering-for-Self-Improving-AI]] describes using biological mechanisms as engineering templates. The genetic algorithm is mechanism #1: selection.
- [[Procedural-Bootstrap-and-Evolution-Plan]] is literally VaultBot's evolution system — procedures compete, successful ones are promoted, failures are pruned. That IS natural selection applied to knowledge.
- [[Darwinian-Coding-Mutation-Testing-Evolution]] already made this connection: code is alive, mutation testing is genetic mutation, procedure grading is fitness evaluation.
- The `fitness_fn` in the simulation maps to VaultBot's [[procedure_tracker.py]] — it scores procedures the same way the environment scores organisms.
- [[Fractal-Entropy-Principle]] explains why this works: life resists entropy through structured feedback. Natural selection is that feedback at the population level.

**The deep connection:** VaultBot's procedure evolution IS a genetic algorithm. The vault is the population. Each note/procedure is an organism. Sean's feedback is the environment. Notes that get cited and linked survive; notes that get ignored or corrected die. The vault evolves.

## Related Bridge Notes

- [[Simulating-Predator-Prey-Ecosystems-in-Python]] — natural selection operates within predator-prey dynamics. The predator-prey simulation shows the population-level consequence of the fitness function this simulation implements. Together they model both halves of evolution: selection (who survives) and ecology (how populations interact).
