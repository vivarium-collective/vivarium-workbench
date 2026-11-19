// templates/_assets/render-helpers.js
function renderJsonTree(obj, depth) {
  depth = depth || 0;
  if (obj === null) return '<span class="bool">null</span>';
  if (typeof obj === "string") return '<span class="str">"' + escapeHtml(obj) + '"</span>';
  if (typeof obj === "number") return '<span class="num">' + obj + '</span>';
  if (typeof obj === "boolean") return '<span class="bool">' + obj + '</span>';
  if (Array.isArray(obj)) {
    if (obj.length === 0) return "[]";
    var inner = obj.map(function (v) { return renderJsonTree(v, depth + 1); }).join(", ");
    return "[<div style=\"margin-left:1em\">" + inner + "</div>]";
  }
  if (typeof obj === "object") {
    var entries = Object.entries(obj);
    if (entries.length === 0) return "{}";
    var open = depth >= 2 ? "▸" : "▾";
    var openAttr = depth < 2 ? " open" : "";
    var body = entries.map(function (kv) {
      return '<div><span class="key">' + escapeHtml(kv[0]) + ':</span> ' + renderJsonTree(kv[1], depth + 1) + '</div>';
    }).join("");
    return '<details' + openAttr + '><summary>' + open + ' {…}</summary><div style="margin-left:1em">' + body + '</div></details>';
  }
  return String(obj);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];
  });
}
window.renderJsonTree = renderJsonTree;
