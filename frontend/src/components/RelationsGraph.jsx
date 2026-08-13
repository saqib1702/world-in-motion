import { useEffect, useMemo, useRef } from "react";
import * as d3 from "d3";

function relationColor(score) {
  if (score >= 40) return "#5ad17f";
  if (score >= 10) return "#8adca4";
  if (score <= -40) return "#ff5f5f";
  if (score <= -10) return "#ff8f8f";
  return "#8fa1b4";
}

export default function RelationsGraph({ agents, relations, selectedAgentId, onSelectAgent }) {
  const svgRef = useRef(null);

  const nodes = useMemo(
    () => agents.map((agent) => ({ id: agent.agent_id, label: agent.name })),
    [agents]
  );

  const links = useMemo(
    () => relations.map((rel) => ({ source: rel.source_agent_id, target: rel.target_agent_id, score: Number(rel.score || 0) })),
    [relations]
  );

  useEffect(() => {
    if (!svgRef.current) return;

    const width = svgRef.current.clientWidth || 900;
    const height = 520;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${width} ${height}`);

    const defs = svg.append("defs");
    const glow = defs.append("filter").attr("id", "nodeGlow");
    glow.append("feGaussianBlur").attr("stdDeviation", "2.6").attr("result", "coloredBlur");
    const merge = glow.append("feMerge");
    merge.append("feMergeNode").attr("in", "coloredBlur");
    merge.append("feMergeNode").attr("in", "SourceGraphic");

    const simulation = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((d) => d.id).distance(140).strength(0.4))
      .force("charge", d3.forceManyBody().strength(-500))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(36));

    const g = svg.append("g");

    const link = g
      .append("g")
      .attr("stroke-linecap", "round")
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("stroke", (d) => relationColor(d.score))
      .attr("stroke-opacity", 0.88)
      .attr("stroke-width", (d) => Math.max(1.2, Math.abs(d.score) / 15));

    const node = g
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .enter()
      .append("circle")
      .attr("r", 17)
      .attr("fill", (d) => (d.id === selectedAgentId ? "#e4b560" : "#6ea2d9"))
      .attr("stroke", "#e6edf5")
      .attr("stroke-width", (d) => (d.id === selectedAgentId ? 2.6 : 1.4))
      .style("cursor", "pointer")
      .style("filter", "url(#nodeGlow)")
      .on("click", (_, d) => onSelectAgent(d.id))
      .call(
        d3
          .drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.15).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    const labels = g
      .append("g")
      .selectAll("text")
      .data(nodes)
      .enter()
      .append("text")
      .text((d) => d.label)
      .attr("font-size", "12px")
      .attr("font-weight", 600)
      .attr("fill", "#f6f8fb")
      .attr("text-anchor", "middle")
      .attr("dy", -24)
      .style("pointer-events", "none");

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);

      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);

      labels.attr("x", (d) => d.x).attr("y", (d) => d.y);
    });

    return () => simulation.stop();
  }, [nodes, links, selectedAgentId, onSelectAgent]);

  return (
    <div className="graph-shell">
      <div className="panel-title">Diplomatic Relations Network</div>
      <svg ref={svgRef} className="graph-svg" />
      <div className="graph-legend">
        <span><i className="legend-dot positive" /> allied</span>
        <span><i className="legend-dot neutral" /> neutral</span>
        <span><i className="legend-dot hostile" /> hostile</span>
      </div>
    </div>
  );
}
