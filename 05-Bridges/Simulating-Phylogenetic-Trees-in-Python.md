---
type: bridge
status: complete
created: 2026-07-29
summary: "Python simulation of phylogenetic tree construction — building evolutionary relationship trees from genetic distance data using UPGMA. The same tree-building process described in Phylogenetic-Trees-and-Dichotomous-Keys, implemented as code. Bridge between biology cluster and Python cluster."
tags: [bridge, biology, python, phylogenetic, tree, evolution, taxonomy, simulation, biomimetic]
biology_links:
  - "[[Phylogenetic-Trees-and-Dichotomous-Keys]]"
  - "[[Evolution-and-the-Long-Arc]]"
  - "[[Evolution-and-Population-Genetics]]"
  - "[[Reproduction-and-Genetic-Inheritance]]"
  - "[[Adaptation-and-Natural-Selection]]"
python_links:
  - "[[Python-3.11-Playbook]]"
  - "[[What-Is-A-Bit]]"
  - "[[Exemplar-Tool-Creation]]"
---

# Simulating Phylogenetic Trees in Python

## The Bridge

[[Phylogenetic-Trees-and-Dichotomous-Keys]] describes how biologists build trees of evolutionary relationships — nodes represent common ancestors, branches represent lineages, and leaves represent living species. [[Evolution-and-the-Long-Arc]] explains how populations diverge over time through mutation, selection, and drift. The distance between species on the tree reflects how long ago they shared a common ancestor.

This note implements phylogenetic tree construction in Python using the **UPGMA algorithm** (Unweighted Pair Group Method with Arithmetic Mean) — the simplest distance-based tree-building method. Given a matrix of genetic distances between species, it builds a tree by repeatedly clustering the closest pairs.

| Biology | Python |
|---|---|
| Species | Leaf nodes in the tree |
| Genetic distance | Distance matrix entries |
| Common ancestor | Internal node |
| Speciation event | Node where a branch splits |
| Cladistics | The clustering algorithm |
| Dichotomous key | Tree traversal for identification |

[[Evolution-and-Population-Genetics]] explains the four mechanisms that create genetic distance — mutation, selection, drift, gene flow. The distance matrix in the simulation is the accumulated result of those mechanisms. [[Reproduction-and-Genetic-Inheritance]] explains how traits pass to offspring — the tree shows that inheritance path.

## The Simulation

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

@dataclass
class TreeNode:
    """A node in a phylogenetic tree — either a leaf (species) or internal (ancestor)."""
    name: str
    children: List['TreeNode'] = field(default_factory=list)
    parent: Optional['TreeNode'] = None
    height: float = 0.0  # Distance from root (evolutionary time)
    
    def is_leaf(self) -> bool:
        return len(self.children) == 0
    
    def to_newick(self) -> str:
        """Convert tree to Newick format (standard phylogenetic tree notation)."""
        if self.is_leaf():
            return self.name
        children_str = ",".join(child.to_newick() for child in self.children)
        if self.parent is None:
            return f"({children_str});"
        return f"({children_str}):{self.height:.2f}"


def build_phylogenetic_tree(
    species_names: List[str],
    distance_matrix: List[List[float]],
) -> TreeNode:
    """
    Build a phylogenetic tree using UPGMA (Unweighted Pair Group Method
    with Arithmetic Mean).
    
    Algorithm:
    1. Each species starts as its own cluster
    2. Find the two closest clusters (smallest distance)
    3. Merge them into a new node (their common ancestor)
    4. Update distances: new cluster's distance to others = weighted average
    5. Repeat until all species are in one tree
    
    This is how [[Phylogenetic-Trees-and-Dichotomous-Keys]] describes
    cladistics — grouping by shared derived characteristics.
    """
    n = len(species_names)
    
    # Initialize: each species is a leaf node
    nodes: Dict[int, TreeNode] = {}
    clusters: Dict[int, List[int]] = {}
    for i, name in enumerate(species_names):
        nodes[i] = TreeNode(name=name, height=0.0)
        clusters[i] = [i]
    
    # Distance dict with sorted tuple keys
    dist: Dict[Tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            dist[(i, j)] = distance_matrix[i][j]
    
    next_id = n  # Next available node ID
    
    def get_dist(a: int, b: int) -> float:
        """Get distance between two cluster IDs, trying both orderings."""
        return dist.get((min(a, b), max(a, b)), float('inf'))
    
    while len(clusters) > 1:
        # Find the closest pair of clusters
        min_dist = float('inf')
        min_pair = None
        cluster_ids = list(clusters.keys())
        for idx_i in range(len(cluster_ids)):
            for idx_j in range(idx_i + 1, len(cluster_ids)):
                ci, cj = cluster_ids[idx_i], cluster_ids[idx_j]
                d = get_dist(ci, cj)
                if d < min_dist:
                    min_dist = d
                    min_pair = (ci, cj)
        
        if min_pair is None:
            break
        
        i, j = min_pair
        merge_height = min_dist / 2  # Height = half the distance
        
        # Create new ancestor node
        new_node = TreeNode(
            name=f"Node_{next_id}",
            children=[nodes[i], nodes[j]],
            height=merge_height,
        )
        nodes[i].parent = new_node
        nodes[j].parent = new_node
        nodes[i].height = merge_height
        nodes[j].height = merge_height
        
        # Merge clusters
        new_cluster_id = next_id
        clusters[new_cluster_id] = clusters[i] + clusters[j]
        nodes[new_cluster_id] = new_node
        
        # Update distances using arithmetic mean (UPGMA)
        all_ids = [cid for cid in clusters if cid != i and cid != j and cid != new_cluster_id]
        for k in all_ids:
            d_ik = get_dist(i, k)
            d_jk = get_dist(j, k)
            size_i = len(clusters[i])
            size_j = len(clusters[j])
            new_dist = (size_i * d_ik + size_j * d_jk) / (size_i + size_j)
            dist[(min(new_cluster_id, k), max(new_cluster_id, k))] = new_dist
        
        # Remove old clusters and distances
        del clusters[i]
        del clusters[j]
        keys_to_remove = [key for key in list(dist.keys()) if i in key or j in key]
        for key in keys_to_remove:
            del dist[key]
        
        next_id += 1
    
    # The last remaining node is the root
    root_id = list(clusters.keys())[0]
    return nodes[root_id]


def print_tree(node: TreeNode, indent: str = "", is_last: bool = True):
    """Pretty-print a phylogenetic tree."""
    prefix = "└── " if is_last else "├── "
    print(f"{indent}{prefix}{node.name} (h={node.height:.2f})")
    child_indent = indent + ("    " if is_last else "│   ")
    for i, child in enumerate(node.children):
        print_tree(child, child_indent, i == len(node.children) - 1)


if __name__ == "__main__":
    print("=== Simulating Phylogenetic Tree Construction ===\n")
    
    # Genetic distance matrix for 5 primate species
    # (Lower = more closely related)
    species = ["Human", "Chimp", "Gorilla", "Orangutan", "Gibbon"]
    distances = [
        [0.0, 0.5, 1.0, 1.5, 2.0],  # Human
        [0.5, 0.0, 1.0, 1.5, 2.0],  # Chimp
        [1.0, 1.0, 0.0, 1.3, 1.8],  # Gorilla
        [1.5, 1.5, 1.3, 0.0, 1.6],  # Orangutan
        [2.0, 2.0, 1.8, 1.6, 0.0],  # Gibbon
    ]
    
    tree = build_phylogenetic_tree(species, distances)
    
    print("Tree structure:")
    print_tree(tree)
    
    print(f"\nNewick format: {tree.to_newick()}")
    
    print("\nInterpretation:")
    print("- Human and Chimp are closest (distance 0.5) → share recent common ancestor")
    print("- Gorilla joins next (distance ~1.0) → common ancestor further back")
    print("- Orangutan joins next → even more distant common ancestor")
    print("- Gibbon is most distantly related → earliest to diverge")
    print("\nThis matches the real primate phylogeny from [[Phylogenetic-Trees-and-Dichotomous-Keys]]")
```

## How This Connects

**Biology side:** [[Phylogenetic-Trees-and-Dichotomous-Keys]] describes how to read and build phylogenetic trees — this code implements that process. [[Evolution-and-the-Long-Arc]] explains how populations diverge through speciation — each internal node in the tree is a speciation event. [[Evolution-and-Population-Genetics]] explains the mechanisms creating genetic distance — the distance matrix encodes the accumulated result. [[Reproduction-and-Genetic-Inheritance]] explains how traits pass through lineages — the tree traces that inheritance. [[Adaptation-and-Natural-Selection]] explains why branches survive or die — the tree shows which lineages persisted.

**Python side:** Uses dataclasses with recursive references (TreeNode contains List[TreeNode]), dictionaries with tuple keys, and recursive tree traversal — all from [[Python-3.11-Playbook]]. The recursive `to_newick()` method demonstrates tree serialization. The `print_tree()` function uses Unicode box-drawing characters for visualization. [[What-Is-A-Bit]] shows how information reduces to bits — here, evolutionary relationships (continuous time) become discrete distance values. [[Exemplar-Tool-Creation]] demonstrates the clean API design used here (docstrings, type hints, separation of concerns).

**Biomimetic side:** Phylogenetic trees are how biology organizes its own knowledge — the Tree of Life is biology's version of a knowledge graph. This simulation shows how to build that structure from data, the same way [[Cross-Session-Patterns-from-75-Chat-Logs]] builds semantic structures from chat history. The vault's wikilink graph is itself a phylogenetic tree of ideas — notes diverge from shared ancestors (linked notes) and evolve over time.

## Python Textbook References

This simulation uses:
- [[python-9classes]] — dataclasses with recursive references (TreeNode contains List[TreeNode])
- [[python-5data-structures]] — dictionaries with tuple keys, list operations

## VaultBot Architecture Connection

This simulation maps to how VaultBot organizes its own knowledge:

- [[Biomimetic-Engineering-for-Self-Improving-AI]] identifies phylogenetic organization as a model for knowledge taxonomy.
- The UPGMA algorithm builds trees from distance matrices — the same principle as [[vault_cluster_analyzer]], which builds communities from graph distances. Both reveal the evolutionary structure of a knowledge system.
- [[Phylogenetic-Trees-and-Dichotomous-Keys]] describes how biologists organize species by shared ancestry. The vault organizes notes by shared wikilinks — the same tree structure, different substrate.
- The Newick format output connects to [[How-to-Organize-a-Knowledge-Base]] — both are about representing relationships as tree structures.
- [[Cross-Session-Patterns-from-75-Chat-Logs]] traces how ideas evolve across conversations — that's a phylogeny of thought, the same way a species tree is a phylogeny of organisms.

**The deep connection:** The vault's wikilink graph IS a phylogenetic tree of ideas. Notes diverge from shared ancestors (linked source notes), evolve over time (edits, appends), and speciate (new notes branching off). The [[vault_cluster_analyzer]] is doing phylogenetic analysis on the vault — finding communities the same way UPGMA finds clades. The vault is a Tree of Knowledge, and this simulation shows how to build one from data.

## Related Bridge Notes

- [[Simulating-Slime-Mold-Pathfinding-in-Python]] — both reveal how biological systems organize information without a central controller. The phylogenetic tree shows evolutionary relationships through distance-based clustering; the slime mold shows network optimization through gradient-following. Both are emergent structure from local rules — the same principle behind the vault's self-organizing knowledge graph.
