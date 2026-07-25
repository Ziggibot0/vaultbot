"""
Agent-authored tool: vault_graph_analyzer
"""

SCHEMA = {"name": "vault_graph_analyzer", "description": "Analyze the connectedness of the vault's .md files. Finds islands (connected components), measures hop distances, identifies isolated nodes, and suggests bridge edges to connect disconnected islands. Excludes LICENSE.md by default. Use this to find where the graph is fragmented and what edges to build.", "parameters": {"properties": {"exclude_patterns": {"default": ["LICENSE.md"], "description": "Filenames to exclude from analysis (default: LICENSE.md)", "items": {"type": "string"}, "type": "array"}, "max_hops": {"default": 6, "description": "Maximum hop distance to measure connectivity (default: 6)", "type": "integer"}, "vault_path": {"default": "", "description": "Path to vault root. Defaults to parent of vaultbot_backend/.", "type": "string"}}, "type": "object"}}

import os
import re
from collections import defaultdict, deque

# Directories that contain .md files but are NOT vault knowledge content
EXCLUDE_DIRS = {'vaultbot_venv', '__pycache__', 'node_modules', '.git', '.obsidian', 'partials'}

def find_md_files(vault_path, exclude_patterns=None):
    if exclude_patterns is None:
        exclude_patterns = ['LICENSE.md']
    md_files = []
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith('.md'):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, vault_path)
                basename = f
                if any(basename == pat or pat in rel_path for pat in exclude_patterns):
                    continue
                md_files.append(rel_path)
    return sorted(md_files)

def parse_wikilinks(content):
    pattern = re.compile(r'\[\[([^\]|#]+)')
    matches = pattern.findall(content)
    return [m.strip() for m in matches]

def build_graph(vault_path, exclude_patterns=None):
    if exclude_patterns is None:
        exclude_patterns = ['LICENSE.md']
    md_files = find_md_files(vault_path, exclude_patterns)
    name_to_files = defaultdict(list)
    for fp in md_files:
        basename = os.path.splitext(os.path.basename(fp))[0]
        name_to_files[basename].append(fp)
    all_nodes = set()
    for fp in md_files:
        basename = os.path.splitext(os.path.basename(fp))[0]
        all_nodes.add(basename)
    adj = defaultdict(set)
    for fp in md_files:
        full_path = os.path.join(vault_path, fp)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue
        basename = os.path.splitext(os.path.basename(fp))[0]
        links = parse_wikilinks(content)
        for link in links:
            if link in all_nodes:
                adj[basename].add(link)
                adj[link].add(basename)
    return adj, all_nodes, name_to_files, md_files

def find_components(adj, all_nodes):
    visited = set()
    components = []
    for node in sorted(all_nodes):
        if node not in visited:
            component = set()
            queue = deque([node])
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                for neighbor in adj.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            components.append(component)
    return sorted(components, key=len, reverse=True)

def bfs_distances(adj, source, max_hops=6):
    distances = {source: 0}
    queue = deque([source])
    while queue:
        current = queue.popleft()
        current_dist = distances[current]
        if current_dist >= max_hops:
            continue
        for neighbor in adj.get(current, set()):
            if neighbor not in distances:
                distances[neighbor] = current_dist + 1
                queue.append(neighbor)
    return distances

def suggest_bridges(components, adj, all_nodes):
    suggestions = []
    if len(components) <= 1:
        return suggestions
    for i, comp_a in enumerate(components):
        for j, comp_b in enumerate(components):
            if j <= i:
                continue
            best_a = max(comp_a, key=lambda n: len(adj.get(n, set())))
            best_b = max(comp_b, key=lambda n: len(adj.get(n, set())))
            suggestions.append({
                'from_island': i,
                'to_island': j,
                'from_node': best_a,
                'to_node': best_b,
                'from_degree': len(adj.get(best_a, set())),
                'to_degree': len(adj.get(best_b, set())),
            })
    return suggestions

def analyze_graph(vault_path, exclude_patterns=None, max_hops=6):
    if exclude_patterns is None:
        exclude_patterns = ['LICENSE.md']
    adj, all_nodes, name_to_files, md_files = build_graph(vault_path, exclude_patterns)
    components = find_components(adj, all_nodes)
    total_nodes = len(all_nodes)
    total_edges = sum(len(neighbors) for neighbors in adj.values()) // 2
    num_islands = len(components)
    largest_island = len(components[0]) if components else 0
    isolated = sorted([n for n in all_nodes if len(adj.get(n, set())) == 0])
    avg_degree = round((2 * total_edges / total_nodes), 2) if total_nodes > 0 else 0
    reachable_pairs = 0
    total_pairs = 0
    for node in all_nodes:
        dists = bfs_distances(adj, node, max_hops)
        reachable = sum(1 for d in dists.values() if 0 < d <= max_hops)
        reachable_pairs += reachable
        total_pairs += total_nodes - 1
    connectivity_ratio = round(reachable_pairs / total_pairs, 3) if total_pairs > 0 else 0
    component_details = []
    for i, comp in enumerate(components):
        comp_nodes = sorted(comp)
        if len(comp) > 1:
            max_hop = 0
            for n in comp:
                dists = bfs_distances(adj, n, max_hops=999)
                comp_dists = {k: v for k, v in dists.items() if k in comp}
                if comp_dists:
                    max_hop = max(max_hop, max(comp_dists.values()))
        else:
            max_hop = 0
        component_details.append({
            'island_id': i,
            'size': len(comp),
            'nodes': comp_nodes,
            'max_internal_hops': max_hop,
        })
    bridges = suggest_bridges(components, adj, all_nodes)
    return {
        'total_files': len(md_files),
        'total_nodes': total_nodes,
        'total_edges': total_edges,
        'num_islands': num_islands,
        'largest_island_size': largest_island,
        'isolated_nodes': isolated,
        'avg_degree': avg_degree,
        'connectivity_ratio': connectivity_ratio,
        'max_hops_measured': max_hops,
        'components': component_details,
        'bridge_suggestions': bridges,
    }

def run(args: dict) -> dict:
    vault_path = args.get('vault_path', '')
    if not vault_path:
        vault_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    exclude_patterns = args.get('exclude_patterns', ['LICENSE.md'])
    max_hops = args.get('max_hops', 6)
    try:
        result = analyze_graph(vault_path, exclude_patterns, max_hops)
        return {'status': 'success', 'analysis': result}
    except Exception as e:
        import traceback
        return {'status': 'error', 'message': str(e), 'traceback': traceback.format_exc()}
