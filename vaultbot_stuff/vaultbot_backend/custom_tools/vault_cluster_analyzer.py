"""
Agent-authored tool: vault_cluster_analyzer
"""

SCHEMA = {"name": "vault_cluster_analyzer", "description": "Analyze the vault graph's cluster structure: identifies communities using label propagation, counts cross-cluster edges, finds sparse connection zones, and identifies which nodes should be connected but aren't. Use this to SEE the vault's shape \u2014 where clusters are dense, where connections are thin, and where bridges are needed.", "parameters": {"properties": {"min_cluster_size": {"description": "Minimum cluster size to include in analysis (default 3).", "type": "integer"}, "sparsity_threshold": {"description": "Edges per node ratio below which a cross-cluster zone is flagged as sparse (default 0.1).", "type": "number"}, "vault_path": {"description": "Path to the vault root. Defaults to the parent of vaultbot_backend/.", "type": "string"}}, "type": "object"}}

import os
import re
import random
from collections import defaultdict, Counter
from pathlib import Path
import json

WIKILINK_RE = re.compile(r"\[\[([^\][\|\r\n]+)(?:\|[^\]\r\n]+)?\]\]")
IGNORED_DIRS = {"vaultbot_venv", "vaultbot_index", "sessions", "partials",
                 ".git", ".obsidian", "vaultbot_backend", "vaultbot_stuff", "learningMaterial",
                 "node_modules", ".venv", "baseline"}

def run(args: dict) -> dict:
    vault_path = args.get("vault_path", "")
    min_cluster_size = args.get("min_cluster_size", 3)
    sparsity_threshold = args.get("sparsity_threshold", 0.1)
    
    if not vault_path:
        # Auto-detect: 4 levels up from custom_tools/ = vault root
        # (custom_tools/ -> vaultbot_backend/ -> vaultbot_stuff/ -> the vault root)
        vault_path = str(Path(__file__).resolve().parent.parent.parent.parent)
    
    vault_root = Path(vault_path)
    
    # Build graph
    node_to_path = {}
    node_to_dir = {}
    adj = defaultdict(set)
    nodes = set()
    edges_set = set()
    
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if not f.endswith('.md'):
                continue
            p = Path(root) / f
            if any(part in IGNORED_DIRS for part in p.parts):
                continue
            stem = p.stem.lower().strip()
            nodes.add(stem)
            rel = str(p.relative_to(vault_root))
            node_to_path[stem] = rel
            parts = rel.split('\\') if '\\' in rel else rel.split('/')
            node_to_dir[stem] = parts[0] if len(parts) > 1 else "(root)"
            try:
                content = p.read_text(encoding='utf-8', errors='replace')
            except:
                continue
            clean = re.sub(r'```.*?```', '', content, flags=re.DOTALL)
            for m in WIKILINK_RE.finditer(clean):
                target = m.group(1).strip().lower()
                if target != stem:
                    edges_set.add(tuple(sorted([stem, target])))
                    adj[stem].add(target)
                    adj[target].add(stem)
    
    # Label propagation community detection
    random.seed(42)
    labels = {n: i for i, n in enumerate(nodes)}
    for iteration in range(20):
        changed = False
        node_list = list(nodes)
        random.shuffle(node_list)
        for node in node_list:
            if not adj.get(node):
                continue
            neighbor_labels = defaultdict(int)
            for neighbor in adj[node]:
                if neighbor in labels:
                    neighbor_labels[labels[neighbor]] += 1
            if not neighbor_labels:
                continue
            best = max(neighbor_labels, key=neighbor_labels.get)
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break
    
    communities = defaultdict(list)
    for node, label in labels.items():
        communities[label].append(node)
    sorted_comm = sorted(communities.values(), key=len, reverse=True)
    
    node_to_cluster = {}
    for i, comm in enumerate(sorted_comm):
        for node in comm:
            node_to_cluster[node] = i
    
    # Count cross-cluster edges
    cross_edges = defaultdict(list)
    for edge in edges_set:
        a, b = edge
        ca = node_to_cluster.get(a, -1)
        cb = node_to_cluster.get(b, -1)
        if ca != cb and ca != -1 and cb != -1:
            key = tuple(sorted([ca, cb]))
            cross_edges[key].append((a, b))
    
    # Build cluster profiles
    cluster_profiles = []
    for i, comm in enumerate(sorted_comm):
        if len(comm) < min_cluster_size and i > 5:
            break
        dirs = Counter()
        for node in comm:
            dirs[node_to_dir.get(node, "?")] += 1
        top_dirs = dirs.most_common(3)
        sample_paths = [node_to_path.get(n, n) for n in comm[:5]]
        cluster_profiles.append({
            "cluster_id": i,
            "size": len(comm),
            "top_dirs": [{"dir": d, "count": c, "pct": round(100*c/len(comm))}
                        for d, c in top_dirs],
            "sample_nodes": sample_paths,
        })
    
    # Build cross-cluster edge summary
    cross_summary = []
    for (ca, cb), edge_list in sorted(cross_edges.items(), key=lambda x: len(x[1]), reverse=True):
        cross_summary.append({
            "cluster_a": ca,
            "cluster_b": cb,
            "size_a": len(sorted_comm[ca]) if ca < len(sorted_comm) else 0,
            "size_b": len(sorted_comm[cb]) if cb < len(sorted_comm) else 0,
            "edge_count": len(edge_list),
            "edges": [{"from": node_to_path.get(a, a), "to": node_to_path.get(b, b)}
                      for a, b in edge_list[:10]],
        })
    
    # Find sparse zones
    sparse_zones = []
    for entry in cross_summary:
        combined = entry["size_a"] + entry["size_b"]
        if combined < 20:
            continue
        sparsity = entry["edge_count"] / max(combined, 1)
        if sparsity < sparsity_threshold:
            sparse_zones.append({
                **entry,
                "sparsity_ratio": round(sparsity, 4),
                "verdict": "SPARSE" if entry["edge_count"] < 5 else "THIN",
            })
    
    sparse_zones.sort(key=lambda x: x["edge_count"])
    
    # Build human-readable summary
    lines = []
    lines.append(f"VAULT CLUSTER ANALYSIS")
    lines.append(f"=" * 50)
    lines.append(f"Total nodes: {len(nodes)}")
    lines.append(f"Total edges: {len(edges_set)}")
    lines.append(f"Clusters found: {len(sorted_comm)}")
    lines.append(f"Cross-cluster edges: {sum(len(v) for v in cross_edges.values())}")
    lines.append("")
    lines.append("CLUSTERS (by size):")
    for cp in cluster_profiles[:8]:
        lines.append(f"\n  Cluster {cp['cluster_id']}: {cp['size']} nodes")
        for d in cp['top_dirs']:
            lines.append(f"    {d['dir']}: {d['count']} ({d['pct']}%)")
        lines.append(f"    e.g: {cp['sample_nodes'][0][:60]}")
    
    lines.append("\n\nSPARSE ZONES (need more connections):")
    for zone in sparse_zones[:10]:
        lines.append(f"\n  Cluster {zone['cluster_a']} ({zone['size_a']} nodes) <-> "
                     f"Cluster {zone['cluster_b']} ({zone['size_b']} nodes)")
        lines.append(f"  Edges: {zone['edge_count']} | Sparsity: {zone['sparsity_ratio']} | {zone['verdict']}")
        for e in zone["edges"][:5]:
            lines.append(f"    {e['from'][:45]:45s} -- {e['to'][:45]}")
    
    summary = "\n".join(lines)
    
    return {
        "summary": summary,
        "total_nodes": len(nodes),
        "total_edges": len(edges_set),
        "num_clusters": len(sorted_comm),
        "clusters": cluster_profiles,
        "cross_cluster_edges": cross_summary,
        "sparse_zones": sparse_zones,
        "total_cross_edges": sum(len(v) for v in cross_edges.values()),
    }
