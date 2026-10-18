import json
from pathlib import Path

def generate_interactive_graph():
    store_file = Path(".codemem/store.json")
    if not store_file.exists():
        print("No .codemem/store.json found.")
        return

    data = json.loads(store_file.read_text(encoding="utf-8"))
    
    nodes = []
    edges = []
    
    # Repo Node
    for r in data.get("repositories", []):
        rid = r["repository_id"]
        nodes.append({
            "id": rid,
            "label": f"Repo: {rid[:8]}",
            "group": "repository",
            "title": f"Repository: {r.get('root_path')}<br>Git HEAD: {r.get('head_commit', '')[:8]}"
        })
        
    # Memory Nodes & Edges
    for m in data.get("memories", []):
        mid = m["id"]
        status = m.get("status", "UNKNOWN")
        claim = m.get("claim", "")
        nodes.append({
            "id": mid,
            "label": f"{mid}\n[{status}]",
            "group": f"memory_{status.lower()}",
            "title": f"<b>Claim:</b> {claim}<br><b>Subject:</b> {m.get('subject')}<br><b>Predicate:</b> {m.get('predicate')}<br><b>Object:</b> {m.get('object')}<br><b>Confidence:</b> {m.get('confidence_score')}"
        })
        
        # Edge to repo
        edges.append({
            "from": mid,
            "to": m["repository_id"],
            "label": "SCOPED_TO",
            "arrows": "to"
        })
        
        # Evidence / CodeAnchors
        for ev in m.get("evidence_records", []):
            ev_id = ev.get("id") or f"evi_{abs(hash(ev.get('file_path')))}"
            ev_label = Path(ev.get("file_path", "")).name
            chash = (ev.get("content_hash") or "")[:12]
            if not any(n["id"] == ev_id for n in nodes):
                nodes.append({
                    "id": ev_id,
                    "label": f"File: {ev_label}",
                    "group": "evidence",
                    "title": f"<b>Path:</b> {ev.get('file_path')}<br><b>Symbol:</b> {ev.get('symbol_name') or 'N/A'}<br><b>Hash:</b> {chash}"
                })
            edges.append({
                "from": mid,
                "to": ev_id,
                "label": "GROUNDED_IN",
                "arrows": "to"
            })
            
    # Handoff Nodes & Edges
    for h in data.get("handoffs", []):
        hid = h.get("handoff_id") or h.get("id")
        status = h.get("status", "PENDING")
        nodes.append({
            "id": hid,
            "label": f"{hid}\n[{status}]",
            "group": "handoff",
            "title": f"<b>Objective:</b> {h.get('task_objective')}<br><b>From:</b> {h.get('originating_agent_id')}<br><b>To:</b> {h.get('receiving_agent_id')}<br><b>Status:</b> {status}"
        })
        edges.append({
            "from": hid,
            "to": h["repository_id"],
            "label": "ASSIGNED_IN",
            "arrows": "to"
        })
        
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CodeMem Knowledge Graph Visualizer</title>
  <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #0d1117;
      color: #c9d1d9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      overflow: hidden;
    }}
    #header {{
      padding: 12px 24px;
      background: #161b22;
      border-bottom: 1px solid #30363d;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    #header h2 {{
      margin: 0;
      font-size: 1.15rem;
      color: #58a6ff;
    }}
    #badge {{
      background: #238636;
      color: #ffffff;
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 0.8rem;
      font-weight: bold;
    }}
    #network {{
      width: 100vw;
      height: calc(100vh - 58px);
    }}
    .legend {{
      position: absolute;
      bottom: 20px;
      left: 20px;
      background: rgba(22, 27, 34, 0.9);
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 12px 16px;
      font-size: 0.85rem;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      margin: 4px 0;
    }}
    .legend-color {{
      width: 14px;
      height: 14px;
      border-radius: 50%;
      margin-right: 8px;
    }}
  </style>
</head>
<body>
  <div id="header">
    <h2>🕸️ CodeMem Graph Visualizer (FalkorDB Graph Model)</h2>
    <span id="badge">{len(nodes)} Nodes | {len(edges)} Relationships</span>
  </div>
  <div id="network"></div>
  
  <div class="legend">
    <div style="font-weight:bold; margin-bottom: 6px;">Graph Schema:</div>
    <div class="legend-item"><div class="legend-color" style="background:#58a6ff;"></div> :Repository</div>
    <div class="legend-item"><div class="legend-color" style="background:#2ea043;"></div> :Memory [OBSERVED]</div>
    <div class="legend-item"><div class="legend-color" style="background:#a371f7;"></div> :Memory [INFERRED]</div>
    <div class="legend-item"><div class="legend-color" style="background:#d29922;"></div> :CodeAnchor (File/AST)</div>
    <div class="legend-item"><div class="legend-color" style="background:#f85149;"></div> :AgentHandoff</div>
  </div>

  <script type="text/javascript">
    const nodes = new vis.DataSet({json.dumps(nodes)});
    const edges = new vis.DataSet({json.dumps(edges)});

    const container = document.getElementById('network');
    const data = {{ nodes: nodes, edges: edges }};
    const options = {{
      nodes: {{
        shape: 'dot',
        size: 18,
        font: {{ color: '#f0f6fc', size: 12, face: 'monospace' }},
        borderWidth: 2
      }},
      groups: {{
        repository: {{ color: {{ background: '#1f6feb', border: '#58a6ff' }}, size: 26 }},
        memory_observed: {{ color: {{ background: '#238636', border: '#3fb950' }} }},
        memory_inferred: {{ color: {{ background: '#8957e5', border: '#a371f7' }} }},
        memory_verified: {{ color: {{ background: '#2ea043', border: '#56d364' }} }},
        memory_stale: {{ color: {{ background: '#da3633', border: '#f85149' }} }},
        evidence: {{ color: {{ background: '#9e6a03', border: '#d29922' }}, size: 14 }},
        handoff: {{ color: {{ background: '#bd561d', border: '#f0883e' }}, shape: 'diamond', size: 22 }}
      }},
      edges: {{
        color: {{ color: '#8b949e', highlight: '#58a6ff' }},
        font: {{ color: '#8b949e', size: 10, align: 'middle' }},
        smooth: {{ type: 'continuous' }}
      }},
      physics: {{
        forceAtlas2Based: {{
          gravitationalConstant: -45,
          centralGravity: 0.005,
          springLength: 90,
          springConstant: 0.18
        }},
        maxVelocity: 50,
        solver: 'forceAtlas2Based',
        timestep: 0.35,
        stabilization: {{ iterations: 150 }}
      }}
    }};

    const network = new vis.Network(container, data, options);
  </script>
</body>
</html>"""
    
    out_path = Path("graph_viewer.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"[+] Generated interactive graph visualizer: {out_path.resolve()}")

if __name__ == "__main__":
    generate_interactive_graph()
