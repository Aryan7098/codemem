import html
import json
from pathlib import Path

def generate_interactive_graph(repo_path: Path = Path(".")):
    store_file = repo_path / ".codemem" / "store.json"
    if not store_file.exists():
        print(f"No store found at {store_file}")
        return

    data = json.loads(store_file.read_text(encoding="utf-8"))
    nodes = []
    edges = []

    # 1. Repository Node (Central Root)
    for r in data.get("repositories", []):
        rid = r["repository_id"]
        rname = Path(r.get("root_path", "Repository")).name or "Repository"
        nodes.append({
            "id": rid,
            "label": f"📦 {rname}",
            "group": "repository",
            "info": {
                "Type": "Repository Root Scope",
                "Repository ID": rid,
                "Git HEAD": (r.get("head_commit") or "N/A")[:10],
                "Path": r.get("root_path") or "."
            }
        })

    # 2. Handoff Nodes (Multi-agent coordination)
    for h in data.get("handoffs", []):
        hid = h.get("handoff_id") or h.get("id")
        status = h.get("status", "PENDING")
        obj = h.get("task_objective", "")
        short_obj = obj[:30] + "..." if len(obj) > 30 else obj
        nodes.append({
            "id": hid,
            "label": f"🤝 Handoff\n[{status}]\n{short_obj}",
            "group": "handoff",
            "info": {
                "Type": "Agent Handoff Task",
                "Handoff ID": hid,
                "Status": status,
                "From Agent": h.get("originating_agent_id") or "Unknown",
                "To Agent": h.get("receiving_agent_id") or "Unassigned",
                "Task Objective": obj,
                "Completed Work": h.get("completed_work") or "None"
            }
        })
        edges.append({
            "from": hid,
            "to": h["repository_id"],
            "label": "ASSIGNED_IN",
            "arrows": "to"
        })

    # 3. Memory Nodes & CodeAnchors
    for m in data.get("memories", []):
        mid = m["id"]
        status = m.get("status", "UNKNOWN")
        claim = m.get("claim", "")
        subj = m.get("subject", "Fact")
        short_claim = claim[:30] + "..." if len(claim) > 30 else claim
        
        nodes.append({
            "id": mid,
            "label": f"🧠 {subj}\n{short_claim}",
            "group": f"memory_{status.lower()}",
            "info": {
                "Type": f"Memory [{status}]",
                "Memory ID": mid,
                "Status": status,
                "Subject": subj,
                "Predicate": m.get("predicate") or "N/A",
                "Object": m.get("object") or "N/A",
                "Confidence": f"{m.get('confidence_score', 1.0) * 100:.0f}%",
                "Full Claim": claim
            }
        })
        edges.append({
            "from": mid,
            "to": m["repository_id"],
            "label": "SCOPED_TO",
            "arrows": "to"
        })

        for ev in m.get("evidence_records", []):
            ev_id = ev.get("id") or f"evi_{abs(hash(ev.get('file_path')))}"
            ev_label = Path(ev.get("file_path", "")).name
            chash = (ev.get("content_hash") or "")[:12]
            sym = ev.get("symbol_name") or ""
            sym_display = f"\n({sym})" if sym else ""
            
            if not any(n["id"] == ev_id for n in nodes):
                nodes.append({
                    "id": ev_id,
                    "label": f"📄 {ev_label}{sym_display}",
                    "group": "evidence",
                    "info": {
                        "Type": "Code Anchor (Grounding Evidence)",
                        "File Path": ev.get("file_path"),
                        "Symbol Anchor": sym or "Entire File",
                        "SHA-256 Fingerprint": chash + "..." if chash else "Unset"
                    }
                })
            edges.append({
                "from": mid,
                "to": ev_id,
                "label": "GROUNDED_IN",
                "arrows": "to"
            })

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>CodeMem Knowledge Graph Visualizer</title>
  <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;600&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: radial-gradient(circle at 50% 30%, #0d1527 0%, #030712 100%);
      color: #f8fafc;
      font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
      overflow: hidden;
      height: 100vh;
      width: 100vw;
    }}
    
    /* Top Bar */
    #topbar {{
      height: 64px;
      padding: 0 28px;
      background: rgba(13, 21, 39, 0.8);
      backdrop-filter: blur(16px);
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      z-index: 20;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .brand-logo {{
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: linear-gradient(135deg, #0284c7, #6366f1);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.1rem;
      font-weight: 800;
      color: white;
      box-shadow: 0 0 20px rgba(2, 132, 199, 0.4);
    }}
    .brand-title {{
      font-size: 1.05rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: #ffffff;
    }}
    .brand-sub {{
      font-size: 0.75rem;
      color: #94a3b8;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .stats-row {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .chip {{
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 0.78rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #cbd5e1;
    }}
    .chip.status {{
      background: rgba(34, 197, 94, 0.12);
      border-color: rgba(34, 197, 94, 0.3);
      color: #4ade80;
    }}

    /* Main Canvas */
    #network {{
      width: 100vw;
      height: 100vh;
      padding-top: 64px;
    }}

    /* Modern Slide-in Inspection Panel */
    #drawer {{
      position: absolute;
      top: 80px;
      right: 24px;
      width: 380px;
      max-height: calc(100vh - 120px);
      background: rgba(15, 23, 42, 0.92);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 16px;
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
      padding: 22px;
      z-index: 30;
      display: none;
      overflow-y: auto;
      transition: all 0.2s ease-out;
    }}
    #drawer.active {{
      display: block;
      animation: slideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    @keyframes slideIn {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .drawer-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .drawer-tag {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 3px 8px;
      border-radius: 6px;
      letter-spacing: 0.05em;
    }}
    .close-btn {{
      background: transparent;
      border: none;
      color: #94a3b8;
      cursor: pointer;
      font-size: 1.2rem;
      line-height: 1;
      padding: 2px 6px;
      border-radius: 4px;
    }}
    .close-btn:hover {{
      color: white;
      background: rgba(255,255,255,0.1);
    }}
    .prop-row {{
      margin-bottom: 12px;
    }}
    .prop-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #64748b;
      margin-bottom: 3px;
      font-weight: 700;
    }}
    .prop-val {{
      font-size: 0.88rem;
      color: #e2e8f0;
      word-break: break-word;
      line-height: 1.4;
    }}
    .prop-val.code {{
      font-family: 'Fira Code', monospace;
      font-size: 0.8rem;
      background: rgba(0, 0, 0, 0.3);
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      color: #38bdf8;
    }}

    /* Legend Box */
    .legend {{
      position: absolute;
      bottom: 24px;
      left: 24px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(14px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 14px;
      padding: 16px 20px;
      font-size: 0.8rem;
      z-index: 20;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
    }}
    .legend-title {{
      font-weight: 800;
      color: #94a3b8;
      text-transform: uppercase;
      font-size: 0.68rem;
      letter-spacing: 0.06em;
      margin-bottom: 10px;
    }}
    .legend-grid {{
      display: flex;
      flex-direction: column;
      gap: 7px;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 10px;
      color: #cbd5e1;
    }}
    .legend-dot {{
      width: 12px;
      height: 12px;
      border-radius: 4px;
    }}

    /* Instructions Pill */
    .hints {{
      position: absolute;
      bottom: 24px;
      right: 24px;
      background: rgba(15, 23, 42, 0.8);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 8px 16px;
      border-radius: 24px;
      font-size: 0.78rem;
      color: #94a3b8;
      z-index: 20;
    }}
  </style>
</head>
<body>
  <div id="topbar">
    <div class="brand">
      <div class="brand-logo">C</div>
      <div>
        <div class="brand-title">CodeMem Graph Explorer</div>
        <div class="brand-sub">FalkorDB Property Graph Representation</div>
      </div>
    </div>
    <div class="stats-row">
      <div class="chip status">● FalkorDB Schema Validated</div>
      <div class="chip">Nodes: <b>{len(nodes)}</b></div>
      <div class="chip">Relationships: <b>{len(edges)}</b></div>
    </div>
  </div>

  <div id="network"></div>

  <!-- Click Drawer -->
  <div id="drawer">
    <div class="drawer-header">
      <span id="drawer-badge" class="drawer-tag">Node</span>
      <button class="close-btn" onclick="closeDrawer()">✕</button>
    </div>
    <div id="drawer-content"></div>
  </div>

  <!-- Legend -->
  <div class="legend">
    <div class="legend-title">Graph Node Schema</div>
    <div class="legend-grid">
      <div class="legend-item"><div class="legend-dot" style="background:#0284c7;"></div> :Repository Root</div>
      <div class="legend-item"><div class="legend-dot" style="background:#15803d;"></div> :Memory [OBSERVED]</div>
      <div class="legend-item"><div class="legend-dot" style="background:#7e22ce;"></div> :Memory [INFERRED]</div>
      <div class="legend-item"><div class="legend-dot" style="background:#854d0e;"></div> :CodeAnchor (Source AST)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#c2410c;"></div> :AgentHandoff</div>
    </div>
  </div>

  <div class="hints">
    🖱️ Click any node to open properties inspector | Scroll to zoom
  </div>

  <script type="text/javascript">
    const rawNodes = {json.dumps(nodes)};
    const rawEdges = {json.dumps(edges)};

    const nodeLookup = {{}};
    rawNodes.forEach(n => {{ nodeLookup[n.id] = n; }});

    const nodes = new vis.DataSet(rawNodes);
    const edges = new vis.DataSet(rawEdges);
    const container = document.getElementById('network');

    const options = {{
      nodes: {{
        shape: 'box',
        margin: {{ top: 10, bottom: 10, left: 14, right: 14 }},
        borderWidth: 1.5,
        shadow: {{ enabled: true, color: 'rgba(0,0,0,0.6)', size: 10, x: 0, y: 5 }},
        font: {{
          color: '#ffffff',
          size: 13,
          face: 'Plus Jakarta Sans, -apple-system, sans-serif',
          multi: true,
          bold: {{ color: '#ffffff', size: 13 }}
        }}
      }},
      groups: {{
        repository: {{
          shape: 'box',
          color: {{ background: '#0284c7', border: '#38bdf8', highlight: {{ background: '#0369a1', border: '#7dd3fc' }} }},
          font: {{ size: 14, bold: true }}
        }},
        memory_observed: {{
          shape: 'box',
          color: {{ background: '#15803d', border: '#4ade80', highlight: {{ background: '#166534', border: '#86efac' }} }}
        }},
        memory_inferred: {{
          shape: 'box',
          color: {{ background: '#7e22ce', border: '#c084fc', highlight: {{ background: '#6b21a8', border: '#d8b4fe' }} }}
        }},
        memory_verified: {{
          shape: 'box',
          color: {{ background: '#16a34a', border: '#86efac', highlight: {{ background: '#15803d', border: '#bbf7d0' }} }}
        }},
        memory_stale: {{
          shape: 'box',
          color: {{ background: '#b91c1c', border: '#f87171', highlight: {{ background: '#991b1b', border: '#fca5a5' }} }}
        }},
        evidence: {{
          shape: 'box',
          color: {{ background: '#854d0e', border: '#facc15', highlight: {{ background: '#713f12', border: '#fde047' }} }},
          font: {{ size: 11, color: '#fef08a' }}
        }},
        handoff: {{
          shape: 'box',
          color: {{ background: '#c2410c', border: '#fb923c', highlight: {{ background: '#9a3412', border: '#fdba74' }} }},
          font: {{ size: 12, bold: true }}
        }}
      }},
      edges: {{
        color: {{ color: 'rgba(148, 163, 184, 0.4)', highlight: '#38bdf8', hover: '#60a5fa' }},
        font: {{
          color: '#94a3b8',
          size: 10,
          face: 'Fira Code, monospace',
          align: 'middle',
          background: 'rgba(13, 21, 39, 0.8)'
        }},
        width: 1.5,
        smooth: {{ type: 'cubicBezier', forceDirection: 'none', roundness: 0.3 }}
      }},
      physics: {{
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {{
          gravitationalConstant: -80,
          centralGravity: 0.008,
          springLength: 140,
          springConstant: 0.12,
          damping: 0.9,
          avoidOverlap: 0.85
        }},
        stabilization: {{ iterations: 200 }}
      }},
      interaction: {{
        hover: true,
        zoomView: true,
        dragView: true
      }}
    }};

    const network = new vis.Network(container, {{ nodes, edges }}, options);

    // Click handler for modern Inspector Drawer
    network.on("click", function(params) {{
      if (params.nodes.length > 0) {{
        const nodeId = params.nodes[0];
        const nodeData = nodeLookup[nodeId];
        if (nodeData && nodeData.info) {{
          openDrawer(nodeData);
        }}
      }} else {{
        closeDrawer();
      }}
    }});

    function openDrawer(nodeData) {{
      const drawer = document.getElementById("drawer");
      const badge = document.getElementById("drawer-badge");
      const content = document.getElementById("drawer-content");

      badge.innerText = nodeData.info["Type"] || "NODE";
      badge.style.background = nodeData.group.includes("handoff") ? "#ea580c" : (nodeData.group.includes("repo") ? "#0284c7" : "#16a34a");
      badge.style.color = "white";

      let html = "";
      for (const [key, val] of Object.entries(nodeData.info)) {{
        if (key === "Type") continue;
        const isCode = key.includes("ID") || key.includes("Fingerprint") || key.includes("HEAD") || key.includes("Path");
        html += `
          <div class="prop-row">
            <div class="prop-label">${{key}}</div>
            <div class="prop-val ${{isCode ? 'code' : ''}}">${{val}}</div>
          </div>
        `;
      }}
      content.innerHTML = html;
      drawer.classList.add("active");
    }}

    function closeDrawer() {{
      document.getElementById("drawer").classList.remove("active");
    }}
  </script>
</body>
</html>"""

    out_file = repo_path / "graph_viewer.html"
    out_file.write_text(html_content, encoding="utf-8")
    print(f"[+] Rendered visually enhanced graph: {out_file.resolve()}")

if __name__ == "__main__":
    generate_interactive_graph()
