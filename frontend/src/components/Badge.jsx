import { TYPE_LABEL } from "../api.js";

export function Kind({ type }) {
  return <span className={"kind k-" + type}>{TYPE_LABEL[type] || type}</span>;
}

export function Seal({ file }) {
  const bad = file.integrity === "altered";
  const label = bad ? "Altered" : file.integrity === "intact" ? "Intact" : "Saved";
  return <span className={"seal " + (bad ? "bad" : "ok")}>{label} {file.sha256.slice(0, 8)}</span>;
}
